/*
 * `.js` specifiers that mean `.ts`, for the test runner.
 *
 * The source uses `./i18n/index.js` because that is what the bundler and tsc
 * want from ESM TypeScript. Node's type stripping takes the specifier
 * literally, so importing any module that reaches i18n dies with
 * ERR_MODULE_NOT_FOUND — which is why the i18n test imports the dictionaries
 * directly and cannot touch index.ts at all.
 *
 * One resolve hook fixes it for every test, and only where the .js genuinely
 * does not exist, so a real missing module still reports itself.
 */
import { register } from 'node:module'

register(new URL('./resolve-ts.mjs', import.meta.url))
