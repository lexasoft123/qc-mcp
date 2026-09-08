export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context)
  } catch (err) {
    if (err?.code !== 'ERR_MODULE_NOT_FOUND' || !specifier.endsWith('.js')) throw err
    return next(specifier.slice(0, -3) + '.ts', context)
  }
}
