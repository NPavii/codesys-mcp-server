import { createServer } from "node:http";
import { loadConfig } from "./config.js";
import { createApp } from "./server.js";

const MAX_PORT_ATTEMPTS = 20;

const config = loadConfig();
const app = createApp(config);
const httpServer = createServer(app);

function tryListen(port: number, attemptsLeft: number): void {
  httpServer.listen(port, config.host);

  httpServer.once("listening", () => {
    console.log(`CODESYS MCP server listening on http://${config.host}:${port}/mcp`);
    if (port !== config.port) {
      console.log(`(port ${config.port} was in use; using ${port} instead)`);
    }
  });

  httpServer.once("error", (err: NodeJS.ErrnoException) => {
    if (err.code === "EADDRINUSE") {
      if (attemptsLeft <= 1) {
        console.error(`Could not find a free port after ${MAX_PORT_ATTEMPTS} attempts starting from ${config.port}.`);
        process.exit(1);
      }
      console.warn(`Port ${port} is in use, trying ${port + 1}...`);
      httpServer.removeAllListeners("listening");
      httpServer.removeAllListeners("error");
      tryListen(port + 1, attemptsLeft - 1);
    } else {
      console.error("Server error:", err);
      process.exit(1);
    }
  });
}

tryListen(config.port, MAX_PORT_ATTEMPTS);

function shutdown(signal: string) {
  console.log(`\nReceived ${signal}, shutting down...`);
  httpServer.close(() => {
    console.log("Server closed.");
    process.exit(0);
  });
  setTimeout(() => {
    console.log("Forced shutdown after timeout.");
    process.exit(1);
  }, 10000);
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
