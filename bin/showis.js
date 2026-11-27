#!/usr/bin/env node
// Showis CLI: installs and operates the Docker Compose deployment.
//
// The installer logic lives in install.sh so the curl path and the npm path
// cannot drift; this file only resolves paths, forwards arguments and adds
// lifecycle commands that work from any directory.
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PACKAGE_ROOT = path.resolve(__dirname, "..");
const INSTALLER = path.join(PACKAGE_ROOT, "install.sh");
const COMPOSE_FILE = path.join("infra", "docker-compose.yml");

function version() {
  return JSON.parse(fs.readFileSync(path.join(PACKAGE_ROOT, "package.json"), "utf8")).version;
}

function usage() {
  process.stdout.write(`Showis ${version()}

Usage:
  showis install [options]   clone (or update) and start the Docker Compose stack
  showis up                  build and start the stack
  showis down                stop the stack (volumes are kept)
  showis ps                  show service status
  showis logs [service]      follow logs (default: all services)
  showis password            print the edge (basic auth) password
  showis version             print the version
  showis check               sanity-check the bundled installer

install options:
  --dir PATH       install directory (default ~/.local/share/showis)
  --branch NAME    git branch to install (default main)
  --no-start       prepare infra/.env without starting
  --dry-run        print what would happen, change nothing

The same installer runs from the curl one-liner:
  curl -fsSL https://raw.githubusercontent.com/l1secx/showis/main/install.sh | bash
`);
}

function fail(message) {
  process.stderr.write(`[HATA] ${message}\n`);
  process.exit(1);
}

function hasFile(file) {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

function findDeployment() {
  const roots = [
    process.env.SHOWIS_INSTALL_DIR,
    path.join(os.homedir(), ".local", "share", "showis"),
    process.cwd(),
  ];
  for (const root of roots) {
    if (root && hasFile(path.join(root, COMPOSE_FILE))) return root;
  }
  fail("No Showis deployment found. Install it first (showis install) or set SHOWIS_INSTALL_DIR.");
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: "inherit", ...options });
  if (result.error) fail(result.error.message);
  if (result.signal) process.kill(process.pid, result.signal);
  process.exit(result.status === null ? 1 : result.status);
}

function install(args) {
  if (process.platform === "win32") {
    fail("The installer needs bash. Use WSL 2 and run 'showis install' there.");
  }
  run("bash", [INSTALLER, ...args]);
}

function compose(args) {
  run("docker", ["compose", "-f", COMPOSE_FILE, ...args], { cwd: findDeployment() });
}

function password() {
  const file = path.join(findDeployment(), "infra", ".secrets", "caddy-basic-auth.txt");
  if (!hasFile(file)) fail(`${file} not found`);
  process.stdout.write(fs.readFileSync(file, "utf8"));
}

function check() {
  if (!hasFile(INSTALLER)) fail("install.sh is missing from the package");
  if (process.platform !== "win32") {
    const syntax = spawnSync("bash", ["-n", INSTALLER], { stdio: "inherit" });
    if (syntax.status !== 0) fail("install.sh has a syntax error");
  }
  process.stdout.write(`showis ${version()}: installer ok\n`);
}

function main(argv) {
  const [command, ...rest] = argv;
  switch (command) {
    case "install":
      return install(rest);
    case "up":
      return compose(["up", "-d", "--build", ...rest]);
    case "down":
      return compose(["down", ...rest]);
    case "ps":
      return compose(["ps", ...rest]);
    case "logs":
      return compose(["logs", "-f", ...rest]);
    case "password":
      return password();
    case "version":
    case "--version":
    case "-v":
      process.stdout.write(`${version()}\n`);
      return;
    case "check":
    case "--check":
      return check();
    case "help":
    case "--help":
    case "-h":
    case undefined:
      return usage();
    default:
      fail(`unknown command: ${command}\nRun 'showis help' for the command list.`);
  }
}

main(process.argv.slice(2));
