import { cc } from "bun:ffi"

const source = process.argv[2]
if (!source) throw new Error("Usage: bun runtime-probe.ts <probe-c-source>")

let jscMessage = ""
try {
  JSON.parse("{")
} catch (error) {
  jscMessage = error instanceof Error ? error.message : String(error)
}

let tinyccValue: number | null = null
let tinyccError = ""
try {
  const library = cc({
    source,
    symbols: { virillio_lgpl_probe: { returns: "int", args: [] } },
  })
  try {
    tinyccValue = library.symbols.virillio_lgpl_probe()
  } finally {
    library.close()
  }
} catch (error) {
  tinyccError = error instanceof Error ? error.message : String(error)
}

const report = {
  bunVersion: Bun.version,
  bunRevision: Bun.revision,
  platform: process.platform,
  arch: process.arch,
  jscMessage,
  tinyccValue,
  tinyccError,
  modifiedJavaScriptCoreVerified: jscMessage.startsWith("Virillio LGPL rebuild probe:"),
  modifiedTinyCCVerified: tinyccValue === 20260906 && tinyccError === "",
}

if (!report.modifiedJavaScriptCoreVerified || !report.modifiedTinyCCVerified)
  throw new Error("The rebuilt runtime did not retain both source modification probes")

console.log(JSON.stringify(report, null, 2))
