import { loadConfig } from "./config.js";
import { SERVER_NAME, SERVER_VERSION } from "./constants.js";
import { CapillusStore } from "./store.js";
import { runStdioServer } from "./server.js";

async function main(): Promise<void> {
  const command = process.argv[2] ?? "serve";
  if (command === "serve") {
    await runStdioServer();
    return;
  }
  if (command === "inspect") {
    inspect();
    return;
  }
  throw new Error(`Unknown command: ${command}`);
}

function inspect(): void {
  const config = loadConfig();
  const store = new CapillusStore(config);
  const state = store.readState();
  console.log(
    JSON.stringify(
      {
        server: SERVER_NAME,
        version: SERVER_VERSION,
        config,
        state,
        today_sessions: store.todaySessions()
      },
      null,
      2
    )
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
