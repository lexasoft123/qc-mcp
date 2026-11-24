"""Offline regressions using decoded CorOS wire shapes and reusable fake state."""
from contextlib import nullcontext
import io
import json
import os
import queue
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import autolevel, catalog, leveling, preset, protocol as P, server
from qc_mcp.transport import QCError, QuadCortex


FOLDER = "/media/p4/Presets/My Presets"


class OfflineDevice(QuadCortex):
    def __init__(self):
        super().__init__(io=object())
        self.pointer = True
        self.directory_size = 256
        self.folder_flag = None
        self.send_ack = True
        self.commit = True
        self.modify_io = lambda _: None
        self.file_name = ""
        self.date = 0
        self.block_deletes = False
        self.sent = []
        self.bp = preset.BinaryPreset()
        for row in range(4):
            self.bp.chains.add(in_portid=1 if row == 0 else 0,
                               out_portid=19 if row == 0 else 0)

    def _collect(self, _):
        pass

    def frame(self, command, response):
        self._pending.append((P.NAME_TO_CMD[command], response, b"",
                              response.SerializeToString()))

    def send(self, command, message, proto_bytes=None):
        self.sent.append((command, message))
        cls = type(message)
        rid = message.request_id
        if command == "SetlistPosition":
            stale = cls(request_id=rid - 1, folder_key=FOLDER, position=0)
            self.frame(command, stale)
            if self.pointer:
                self.frame(command, cls(request_id=rid, folder_key=FOLDER, position=0))
        elif command == "RecallPreset":
            self.frame(command, cls(request_id=rid, preset=self.bp))
        elif command == "File" and message.action == P.ACTION["READ"]:
            response = cls(request_id=rid, action=P.ACTION["UPDATE"], type=0)
            response.folder.key = FOLDER
            if self.folder_flag:
                setattr(response.folder, self.folder_flag, True)
            for index in range(self.directory_size):
                f = response.folder.files.add(index=index)
                if index == 0 and self.file_name:
                    f.name = self.file_name
                    f.key = f"{FOLDER}/{self.file_name}.pb"
                    f.date_ms_since_epoch = self.date
            self.frame(command, response)
        elif command == "File" and message.action == P.ACTION["CREATE"]:
            target = message.folder.files[0]
            date = self.date + 1
            if self.commit:
                self.file_name, self.date = target.name, date
            if self.send_ack:
                ack = cls(request_id=rid, action=P.ACTION["CREATE"], type=0)
                ack.folder.key = FOLDER
                ack.folder.files.add(index=target.index, name=target.name,
                                     key=f"{FOLDER}/{target.name}.pb",
                                     date_ms_since_epoch=date)
                self.frame(command, ack)
        elif command == "IOSettings":
            echo = cls(request_id=rid - 1, action=P.ACTION["UPDATE"])
            echo.settings.in_port.add(input_port_id=1, level=0.9)
            self.frame(command, echo)
            response = cls(request_id=rid, action=P.ACTION["UPDATE"],
                           xlr1_2_linked=False, out3_4_linked=False)
            for ident in (1, 2, 4, 5):
                p = response.settings.in_port.add(input_port_id=ident, level=0.25,
                                                  ground_lift=0.0)
                if ident in (1, 2):
                    p.input_type = p.input_zmode = 0.0
            for ident in (1, 2, 4, 5, 6, 7, 8, 9):
                p = response.settings.out_port.add(output_port_id=ident, level=0.75)
                if ident in (1, 4, 5):
                    p.ground_lift = 0.0
                if ident in (1, 2, 4, 5, 6, 7):
                    p.mute = False
            settings = response.settings
            settings.hp_port.level = 0.5
            settings.hp_port.hp_feed.add(output_port_id=1, level=0.5)
            settings.midi_port.midi_thru = 0.0
            settings.usb_port.level = settings.usb_port.hp_select = settings.usb_port.dry_wet = 0.5
            for ident in (1, 2):
                settings.exp_port.add(exp_port_id=ident, level=0.0, plugged=False, calibrating=False)
            self.modify_io(response)
            self.frame(command, response)

    def creates(self):
        return [m for c, m in self.sent if c == "File" and m.action == P.ACTION["CREATE"]]


class SaveTests(unittest.TestCase):
    def test_empty_slot_then_only_explicit_overwrite_including_same_name(self):
        qc = OfflineDevice()
        result = qc.save_verified("A")
        self.assertEqual((result["position"], result["name"]), (0, "A"))
        for name in ("A", "B"):
            with self.assertRaisesRegex(QCError, "occupied"):
                qc.save_verified(name)
        self.assertEqual(len(qc.creates()), 1)
        self.assertEqual(qc.save_verified("A", overwrite=True)["replaced"], "A")
        self.assertEqual(len(qc.creates()), 2)

    def test_partial_listing_never_authorizes_save_even_with_overwrite(self):
        qc = OfflineDevice()
        qc.directory_size = 1
        with self.assertRaisesRegex(QCError, "complete writable setlist"):
            qc.save_verified("A", overwrite=True)
        self.assertFalse(qc.creates())

    def test_nonlocal_folder_flags_reject_even_a_complete_destination_table(self):
        for flag in ("is_plugin", "is_cloud", "is_favorites"):
            with self.subTest(flag=flag):
                qc = OfflineDevice()
                qc.folder_flag = flag
                with self.assertRaisesRegex(QCError, "complete writable setlist"):
                    qc.save_verified("A", FOLDER, 0, overwrite=True)
                self.assertFalse(qc.creates())

    def test_unknown_pointer_sends_no_create_even_with_explicit_destination(self):
        qc = OfflineDevice()
        with patch.object(qc, "get_setlist_position", return_value=None):
            with self.assertRaisesRegex(QCError, "active local setlist"):
                qc.save_verified("A", FOLDER, 0, overwrite=True)
        self.assertFalse(qc.creates())

    def test_nonlocal_source_requires_and_accepts_an_explicit_local_destination(self):
        sources = [
            {"is_downloads": True, "downloads_key": "download-id"},
            {"is_plugin": True, "folder_key": "/plugins/p", "plugin_key": "/plugins/p/a.pb"},
        ]
        for source in sources:
            qc = OfflineDevice()
            with patch.object(qc, "get_setlist_position", return_value=source):
                for kwargs in ({}, {"folder_key": FOLDER}, {"position": 0}):
                    with self.assertRaisesRegex(QCError, "explicit destination"):
                        qc.save_verified("A", **kwargs)
                self.assertFalse(qc.creates())
                saved = qc.save_verified("A", FOLDER, 0)
            self.assertEqual(saved["folder_key"], FOLDER)
            self.assertEqual(len(qc.creates()), 1)

    def test_same_name_requires_both_a_fresh_confirmation_and_readback(self):
        qc = OfflineDevice()
        qc.save_verified("A")
        qc.commit = False
        with self.assertRaisesRegex(QCError, "could not be verified"):
            qc.save_verified("A", overwrite=True)
        qc.send_ack = False
        qc.commit = True
        real_ack = qc._save_confirmation
        with patch.object(qc, "_save_confirmation",
                          side_effect=lambda *args: real_ack(*args, timeout_s=0.001)):
            with self.assertRaisesRegex(QCError, "did not confirm"):
                qc.save_verified("A", overwrite=True)

    def test_single_preset_leveling_save_uses_original_verified_pointer(self):
        for tool, worker in ((server.level_preset, "level_current"),
                             (server.level_scenes, "level_scenes")):
            qc = OfflineDevice()
            qc.save_verified("A")
            with patch.object(server, "_conn", return_value=qc), patch.object(
                    autolevel, worker, return_value={"written": True}) as level:
                options = {"apply": True} if worker == "level_current" else {}
                result = tool(save=True, **options)
            self.assertEqual(level.call_args.kwargs["expected"]["position"], 0)
            self.assertIn("Saved", result["saved"])
            self.assertEqual(len(qc.creates()), 2)

    def test_route_restore_failure_blocks_save(self):
        qc = OfflineDevice()
        from unittest.mock import Mock

        def set_routing(row, in_portid=None, out_portid=None):
            if in_portid == 1:
                raise QCError("input restore rejected")
            if in_portid is not None:
                qc.bp.chains[row].in_portid = in_portid
            if out_portid is not None:
                qc.bp.chains[row].out_portid = out_portid
        qc.set_routing = Mock(side_effect=set_routing)
        with patch.object(autolevel, "ROUTING_SETTLE_S", 0):
            with self.assertRaisesRegex(autolevel.LevelingError, "could not restore"):
                with autolevel.reamp_routing(qc):
                    pass
        self.assertEqual(qc.bp.chains[0].out_portid, 19)
        with self.assertRaisesRegex(QCError, "reamp"):
            qc.save_verified("A")
        self.assertFalse(qc.creates())


class OtherSafetyTests(unittest.TestCase):
    def test_first_gated_tool_negotiates_before_testing_both_generations(self):
        self.addCleanup(P.set_version, P.active_version())
        for previous, actual in (("4.1", "4.0"), ("4.0", "4.1")):
            P.set_version(previous)
            qc = OfflineDevice()

            def connect():
                P.set_version(actual)
                return qc

            with patch.object(server, "_conn", side_effect=connect) as conn, patch.object(
                    qc, "list_model_presets", return_value=[]) as list_presets:
                result = server.list_device_presets()
            conn.assert_called_once_with()
            if actual == "4.0":
                self.assertIn("needs CorOS 4.1", result["error"])
                list_presets.assert_not_called()
            else:
                self.assertEqual(result["total_presets"], 0)
                list_presets.assert_called_once_with(None)
        self.assertFalse(qc.sent)

    def test_secondary_stomp_after_cold_40_negotiation_sends_nothing(self):
        self.addCleanup(P.set_version, P.active_version())
        P.set_version("4.1")
        qc = OfflineDevice()

        def connect():
            P.set_version("4.0")
            return qc

        with patch.object(server, "_conn", side_effect=connect):
            result = server.assign_stomp(0, 0, "A", kind="secondary")
        self.assertIn("needs CorOS 4.1", result["error"])
        self.assertFalse(qc.sent)

    def test_batch_tools_do_not_measure_or_save_after_failed_recall(self):
        qc = OfflineDevice()
        with patch.object(server, "_conn", return_value=qc), patch.object(
                qc, "recall_verified", side_effect=QCError("wrong folder")) as recall, \
             patch.object(autolevel, "measurement_rows") as compare_measure, \
             patch.object(autolevel, "level_current") as leveling_work, \
             patch.object(qc, "save_verified") as save:
            self.assertIn("wrong folder", server.compare_presets(reference_position=0,
                                                                  setlist_key=FOLDER)["error"])
            self.assertIn("wrong folder", server.suggest_levels([0, 1], setlist_key=FOLDER)["error"])
            self.assertIn("wrong folder", server.level_setlist([0, 1], FOLDER, save=True)["aborted_at"])
        self.assertEqual(recall.call_count, 3)
        compare_measure.assert_not_called()
        leveling_work.assert_not_called()
        save.assert_not_called()

    def test_uncleared_grid_prevents_build_writes(self):
        qc = OfflineDevice()
        qc.bp.chains[0].models.add(hash=16005)
        with patch.object(server, "_conn", return_value=qc), patch("qc_mcp.transport.time.sleep"):
            with self.assertRaisesRegex(QCError, "cleared grid"):
                server.build_preset({"chains": [{"row": 0, "blocks": [{"hash": 777, "col": 0}]}]})
        self.assertTrue(all(not m.preset.chains[0].models[0].hash == 777
                            for c, m in qc.sent if c == "Grid" and m.preset.chains
                            and m.preset.chains[0].models))

    def test_scene_param_and_bypass_stop_before_failed_scene(self):
        qc = OfflineDevice()
        qc._await_scene = lambda scene: scene != 1
        with patch("qc_mcp.transport.time.sleep"):
            for write in (lambda: qc.set_param_scenes(0, 0, 0, [0.1, 0.2, 0.3]),
                          lambda: qc.set_block_bypass(0, 0, scenes=[True, False, True],
                                                      bypass_param=23)):
                qc.sent.clear()
                with self.assertRaisesRegex(QCError, "could not confirm"):
                    write()
                self.assertEqual([m.selected_scene for c, m in qc.sent if c == "Scene"], [0, 1])
                self.assertEqual(sum(c == "Grid" for c, _ in qc.sent), 2)  # assign + scene A

    def test_snapshot_ignores_write_echo_and_preserves_serializable_fields(self):
        self.addCleanup(P.set_version, P.active_version())
        for version in ("4.0", "4.1"):
            P.set_version(version)
            snap = server._io_snapshot(OfflineDevice())
            self.assertNotIn("error", json.dumps(snap, allow_nan=False))
            self.assertEqual(len(snap["in_ports"]), 4)
            self.assertEqual(snap["in_ports"][0]["level"], 0.25)
            self.assertNotIn("type", snap["in_ports"][2])
            self.assertEqual(len(snap["out_ports"]), 8)
            self.assertEqual([p["expPortId"] for p in snap["exp_ports"]], [1, 2])
            self.assertEqual(snap["headphones"]["hpFeed"][0]["outputPortId"], 1)

    def test_io_readout_names_only_verified_full_size_combo_input_types(self):
        qc = OfflineDevice()
        qc.modify_io = lambda msg: setattr(msg.settings.in_port[1], "input_type", 1.0)
        with patch.object(server, "_conn", return_value=qc):
            qc.device_type = "QC"
            full = server.get_io_settings()["inputs"]
            qc.device_type = "ATMA"
            mini = server.get_io_settings()["inputs"]
        self.assertEqual([p["type"] for p in full], ["instrument", "mic", None, None])
        self.assertEqual([p["type"] for p in mini], [0.0, 1.0, None, None])

    def test_correlated_partial_or_nonfinite_io_snapshot_cannot_authorize_a_write(self):
        changes = {
            "wrong output port": lambda msg: setattr(msg.settings.out_port[-1], "output_port_id", 3),
            "missing pairing": lambda msg: msg.ClearField("xlr1_2_linked"),
            "missing ground lift": lambda msg: msg.settings.in_port[-1].ClearField("ground_lift"),
            "missing mute": lambda msg: msg.settings.out_port[1].ClearField("mute"),
            "nonfinite input type": lambda msg: setattr(msg.settings.in_port[1], "input_type", float("nan")),
            "nonfinite headphone level": lambda msg: setattr(msg.settings.hp_port, "level", float("inf")),
            "missing expression": lambda msg: msg.settings.exp_port.pop(),
        }
        for description, modify in changes.items():
            with self.subTest(description=description):
                qc = OfflineDevice()
                qc.modify_io = modify
                qc.list_model_presets = lambda _: [{"id": "1", "name": "Flat", "is_factory": True}]
                with patch.object(server, "_conn", return_value=qc), patch.object(
                        qc, "load_settings_preset") as load:
                    result = server.load_settings_preset("io_settings", "Flat", confirm=True)
                self.assertIn("snapshot", result["error"])
                load.assert_not_called()
                self.assertEqual([m.action for _, m in qc.sent], [P.ACTION["READ"]])

    def test_global_change_requires_undo_snapshot(self):
        qc = OfflineDevice()
        qc.list_model_presets = lambda _: [{"id": "1", "name": "Flat", "is_factory": True}]
        with patch.object(server, "_conn", return_value=qc), patch.object(
                qc, "read_message", return_value=P.message_class("IOSettings")()) as read:
            result = server.load_settings_preset("io_settings", "Flat", confirm=True)
        self.assertIn("error", result)
        self.assertEqual(read.call_count, 1)
        self.assertFalse(qc.sent)

    def test_summary_indexes_grid_and_bypass_and_keeps_typed_scene_params(self):
        qc = OfflineDevice()
        lane = qc.bp.chains[2]
        for _ in range(3):
            lane.models.add()
        capture = lane.models.add(hash=14000)
        for _ in range(5):
            capture.params.add()
        key = "a" * 64 + "Capture Name"
        capture.params.add().param_values.add(string_value=key)
        p = capture.params[0]
        p.scene_mode = True
        p.param_values.add(float_value=0.1)
        p.param_values.add(float_value=0.7)
        for _ in range(3):
            row = qc.bp.bypass.add()
        for _ in range(4):
            col = row.colBypass.add()
        col.sceneMode = True
        col.sceneBypass.add(bypass=True)
        col.sceneBypass.add(bypass=False)
        qc.bp.scene_colors.append(5)
        info = server._preset_summary(qc.bp)
        self.assertEqual([c["row"] for c in info["chains"]], [0, 1, 2, 3])
        block = info["chains"][2]["blocks"][0]
        self.assertIn(key, block["params"].values())
        self.assertEqual(len(next(iter(block["scene_params"].values()))), 2)
        self.assertEqual(block["scene_bypass"], [True, False])
        self.assertEqual(info["scene_colors"], [5])

    def test_cancel_uses_the_queued_batch_token_before_dispatch(self):
        requests = [
            {"id": 1, "op": "measure_many", "presets": [{"position": 2}]},
            {"id": 2, "op": "cancel"},
            {"id": 3, "op": "measure_many"},
        ]
        pending = queue.Queue()
        emitted = []
        with patch("qc_mcp.leveling.sys.stdin", io.StringIO("\n".join(map(json.dumps, requests)))):
            leveling._stdin_reader(pending, emitted.append)
        stopped = pending.get_nowait()
        fresh = pending.get_nowait()
        self.assertTrue(stopped["_cancel"].is_set())
        self.assertFalse(fresh["_cancel"].is_set())
        self.assertEqual(emitted, [{"id": 2, "ok": True, "cancelling": True}])

    def test_existing_scene_gains_use_their_own_values_and_skip_untouched_scenes(self):
        qc = OfflineDevice()
        gain = qc.bp.chains[0].models.add(hash=autolevel.GAIN_HASH)
        param = gain.params.add(scene_mode=True)
        for db in [0, 3, 4, 6, 7, 8, 9, 10]:
            value = catalog.to_norm(gain.hash, autolevel.GAIN_LEVEL, db)
            param.param_values.add(float_value=value)
        measured = [{"lufs_integrated": -21.0, "true_peak_dbtp": -10},
                    {"error": "digital silence"}]
        writes = []
        with patch.object(autolevel, "reamp_routing", return_value=nullcontext()), \
             patch.object(autolevel, "measure", side_effect=measured), \
             patch.object(qc, "_await_scene", return_value=True), \
             patch.object(qc, "set_assigned_param_scenes",
                          side_effect=lambda row, col, idx, vals, **_: writes.append(vals)):
            result = autolevel.level_scenes(qc, scenes=[2, 4])
            self.assertEqual(writes[0].keys(), {2})
            got = catalog.to_display(gain.hash, autolevel.GAIN_LEVEL, writes[0][2])
            self.assertAlmostEqual(got, 7.0, places=4)
            self.assertEqual(result["scenes"][1]["skipped"], "digital silence")
            self.assertFalse(autolevel.level_scenes(qc, scenes=[])["written"])
        self.assertEqual(len(writes), 1)

    def test_full_row_with_only_early_gain_refuses_to_touch_grid(self):
        qc = OfflineDevice()
        qc.bp.chains[0].models.add(hash=autolevel.GAIN_HASH)
        for _ in range(7):
            qc.bp.chains[0].models.add(hash=27)
        with self.assertRaisesRegex(autolevel.LevelingError, "no downstream slot"):
            autolevel.ensure_gain_block(qc, 0)
        self.assertTrue(all(c != "Grid" for c, _ in qc.sent))

    def test_measure_scenes_handles_a_fire_and_forget_scene_update(self):
        from types import SimpleNamespace

        qc = OfflineDevice()
        bench = leveling.Bench(qc)
        fake_audio = SimpleNamespace(sampler=lambda: None)
        with patch.object(leveling, "_audio", return_value=(fake_audio, autolevel, None)), \
             patch.object(autolevel, "reamp_routing", return_value=nullcontext()), \
             patch.object(autolevel, "measure", return_value={"lufs_integrated": -18.0}), \
             patch.object(bench, "current_scene", return_value=0), \
             patch.object(qc, "_await_scene", return_value=True):
            ops = leveling.measure_ops(bench, lambda _: None)
            result = ops["measure_scenes"]({"scenes": [0, 1]})
        self.assertEqual([row["measured"] for row in result["rows"]], [-18.0, -18.0])

    def test_global_eq_read_accepts_implicit_zero_values(self):
        qc = OfflineDevice()
        eq = P.message_class("GlobalEQ")(bypassed=False)
        for idx in range(28):
            eq.parameters.add(parameter_index=idx, value=0)
        with patch.object(qc, "read_state", return_value=eq) as wire:
            params, bypassed = qc.read_global_eq()
        wire.assert_called_once_with("GlobalEQ", strict=True)
        self.assertEqual(params[0], (0, 0.0))
        self.assertFalse(bypassed)

    def test_windows_cold_readiness_observes_live_reports_without_writes(self):
        from unittest.mock import Mock

        backend = Mock()
        data = P.message_to_reports(P.encode_message("GlobalTempo", b""))[0]
        backend.read_reports.return_value = [(data[0], data)]
        with patch.object(server.backend, "open_hid", return_value=backend) as opened:
            self.assertTrue(server._shared_handle_ready())
            backend.read_reports.return_value = []
            self.assertFalse(server._shared_handle_ready())
        opened.assert_called_with(seize=False)
        self.assertEqual(backend.close.call_count, 2)
        backend.set_report.assert_not_called()

    def test_input_with_downstream_empty_output_row_uses_that_row(self):
        qc = OfflineDevice()
        qc.bp.chains[0].out_portid = 16
        qc.bp.chains[1].out_portid = 19  # unrelated path
        qc.bp.chains[2].out_portid = 19
        self.assertEqual(autolevel.measurement_rows(qc), (0, 2))


if __name__ == "__main__":
    unittest.main()
