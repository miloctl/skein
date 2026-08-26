#!/usr/bin/env node

import {
  chmodSync,
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { cp as copy } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const packageName = /^(?:@[A-Za-z0-9][A-Za-z0-9._-]*\/)?[A-Za-z0-9][A-Za-z0-9._-]*$/;
const extensions = process.argv.slice(2);
const nodeMajor = Number(process.versions.node.split(".")[0]);
if (nodeMajor !== 22) {
  throw new Error("This Node release is not supported. Use Node 22.");
}
for (const name of extensions) {
  if (!packageName.test(name)) {
    throw new Error("A frontend extension package name is invalid. Use an npm package name.");
  }
}
if (new Set(extensions).size !== extensions.length) {
  throw new Error("A frontend extension package occurs more than once. Remove the duplicate package.");
}

const hostRoot = realpathSync(fileURLToPath(new URL("..", import.meta.url)));
const workplaceRoot = realpathSync(process.cwd());
const requireFromWorkplace = createRequire(path.join(workplaceRoot, "package.json"));
const requireFromHost = createRequire(path.join(hostRoot, "package.json"));
const stageRoot = path.join(workplaceRoot, ".skein", `frontend-host-${process.pid}`);
const shimRoot = path.join(workplaceRoot, ".skein", `no-package-manager-${process.pid}`);
const distRoot = path.join(workplaceRoot, "dist");
const finalOutput = path.join(distRoot, "frontend");
const temporaryOutput = path.join(distRoot, `.frontend-${process.pid}`);
const backupOutput = path.join(distRoot, `.frontend-previous-${process.pid}`);
const hostManifest = JSON.parse(readFileSync(path.join(hostRoot, "package.json"), "utf8"));
const workplaceManifest = JSON.parse(
  readFileSync(path.join(workplaceRoot, "package.json"), "utf8"),
);
const lockPath = path.join(workplaceRoot, "package-lock.json");
if (!existsSync(lockPath)) {
  throw new Error("The workplace package lock is absent. Run npm ci from a committed package lock.");
}
let workplaceLock;
try {
  workplaceLock = JSON.parse(readFileSync(lockPath, "utf8"));
} catch {
  throw new Error("The workplace package lock is invalid. Regenerate the package lock.");
}
const lockRoot = workplaceLock.packages?.[""];
if (!lockRoot) {
  throw new Error("The workplace package lock has no root package. Regenerate the package lock.");
}
for (const name of ["next", "react", "react-dom"]) {
  const required = hostManifest.dependencies[name];
  if (workplaceManifest.dependencies?.[name] !== required) {
    throw new Error(`${name} must be ${required}. Pin it in the workplace dependencies.`);
  }
}
for (const name of ["postcss", "sharp"]) {
  const required = hostManifest.overrides[name];
  if (workplaceManifest.overrides?.[name] !== required) {
    throw new Error(`${name} override must be ${required}. Add it to the workplace root.`);
  }
}

function packageRoot(name) {
  for (const modulesDir of requireFromWorkplace.resolve.paths(name) ?? []) {
    const current = path.join(modulesDir, name);
    try {
      if (JSON.parse(readFileSync(path.join(current, "package.json"), "utf8")).name === name) {
        const root = realpathSync(current);
        const relative = path.relative(workplaceRoot, root);
        if (relative.startsWith("..") || path.isAbsolute(relative)) {
          throw new Error(
            "A frontend extension package resolves outside the workplace root. Install it inside the workplace root.",
          );
        }
        return root;
      }
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  throw new Error(
    "A frontend extension package is not installed. Add it to the workplace dependencies.",
  );
}

const packageRoots = new Map(
  ["@skein/frontend-host", "@skein/extension-api", ...extensions].map((name) => [
    name,
    packageRoot(name),
  ]),
);
for (const name of [
  "@skein/frontend-host",
  "@skein/extension-api",
  "next",
  "react",
  "react-dom",
]) {
  const declared = workplaceManifest.dependencies?.[name];
  if (!declared) {
    throw new Error(`${name} is not a direct workplace dependency. Add the exact dependency.`);
  }
  if (lockRoot.dependencies?.[name] !== declared) {
    throw new Error(`${name} does not match the workplace package lock. Regenerate the package lock.`);
  }
  const locked = workplaceLock.packages?.[`node_modules/${name}`];
  if (!locked || locked.link || !locked.integrity) {
    throw new Error(`${name} is not integrity-locked. Regenerate the package lock.`);
  }
  const root = packageRoots.get(name) ?? packageRoot(name);
  const installed = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
  if (locked.version !== installed.version) {
    throw new Error(`${name} does not match the installed package. Run npm ci.`);
  }
  if (name.startsWith("@skein/") && !declared.startsWith("file:") && declared !== installed.version) {
    throw new Error(`${name} is not pinned to an exact version. Pin the installed version.`);
  }
}
const installedApi = JSON.parse(
  readFileSync(path.join(packageRoots.get("@skein/extension-api"), "package.json"), "utf8"),
);
if (installedApi.version !== hostManifest.peerDependencies["@skein/extension-api"]) {
  throw new Error("The extension API version does not match the frontend host. Install the required version.");
}
for (const name of ["postcss", "sharp"]) {
  const locked = Object.entries(workplaceLock.packages ?? {}).filter(([packagePath]) =>
    packagePath.endsWith(`node_modules/${name}`),
  );
  if (
    locked.length === 0 ||
    locked.some(([, entry]) => entry.version !== hostManifest.overrides[name])
  ) {
    throw new Error(`${name} does not match the required override. Regenerate the package lock.`);
  }
}

let activeChild = null;
let receivedSignal = null;

function terminate(child, signal) {
  try {
    if (process.platform !== "win32" && child.pid) {
      process.kill(-child.pid, signal);
    } else {
      child.kill(signal);
    }
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
}

function handleSignal(signal) {
  receivedSignal ??= signal;
  if (activeChild) terminate(activeChild, signal);
}

function stopIfSignaled() {
  if (receivedSignal) {
    throw new Error("The frontend build was cancelled. Run the command again.");
  }
}

async function runNode(script, args, env) {
  const child = spawn(process.execPath, [script, ...args], {
    cwd: stageRoot,
    detached: process.platform !== "win32",
    env,
    stdio: "inherit",
  });
  activeChild = child;
  if (receivedSignal) terminate(child, receivedSignal);
  try {
    await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => {
        if (code === 0) resolve();
        else {
          reject(
            new Error(
              `${path.basename(script)} stopped with ${signal ?? `status ${code}`}. Read the output and fix the error.`,
            ),
          );
        }
      });
    });
  } finally {
    if (activeChild === child) activeChild = null;
  }
}

function makePackageManagerShims() {
  mkdirSync(shimRoot, { recursive: true });
  for (const command of ["npm", "pnpm", "yarn", "bun"]) {
    const target = path.join(shimRoot, command);
    writeFileSync(target, `#!/bin/sh\necho '${command} cannot run during skein-frontend-build. Install dependencies before you run this command.' >&2\nexit 97\n`);
    chmodSync(target, 0o755);
    if (process.platform === "win32") {
      writeFileSync(`${target}.cmd`, `@echo off\necho ${command} cannot run during skein-frontend-build. Install dependencies before you run this command. 1>&2\nexit /b 97\n`);
    }
  }
}

function copyHost() {
  rmSync(stageRoot, { recursive: true, force: true });
  cpSync(hostRoot, stageRoot, {
    recursive: true,
    filter(source) {
      const first = path.relative(hostRoot, source).split(path.sep)[0];
      return !["node_modules", ".next", "dist"].includes(first);
    },
  });
  const nestedModules = path.join(hostRoot, "node_modules");
  if (existsSync(nestedModules)) {
    symlinkSync(nestedModules, path.join(stageRoot, "node_modules"), "junction");
  }
  for (const name of [".env.production.local", ".env.local", ".env.production", ".env"]) {
    const source = path.join(workplaceRoot, name);
    if (existsSync(source)) cpSync(source, path.join(stageRoot, name));
  }
}

async function promoteStandalone() {
  const standalone = path.join(stageRoot, ".next", "standalone");
  if (!existsSync(standalone)) {
    throw new Error("Next did not produce standalone output. Read the build output and fix the error.");
  }
  mkdirSync(distRoot, { recursive: true });
  rmSync(temporaryOutput, { recursive: true, force: true });
  await copy(standalone, temporaryOutput, { recursive: true });
  stopIfSignaled();

  const nested = path.join(temporaryOutput, path.relative(workplaceRoot, stageRoot));
  if (!existsSync(path.join(nested, "server.js"))) {
    throw new Error(
      "The staged standalone server is absent. Read the build output and fix the error.",
    );
  }
  for (const entry of readdirSync(nested)) {
    const source = path.join(nested, entry);
    const target = path.join(temporaryOutput, entry);
    if (existsSync(target)) {
      cpSync(source, target, { recursive: true, force: true });
      rmSync(source, { recursive: true, force: true });
    } else {
      renameSync(source, target);
    }
  }
  rmSync(path.join(temporaryOutput, ".skein"), { recursive: true, force: true });
  for (const name of [".env.production.local", ".env.local", ".env.production", ".env"]) {
    rmSync(path.join(temporaryOutput, name), { force: true });
  }
  cpSync(
    path.join(stageRoot, ".next", "static"),
    path.join(temporaryOutput, ".next", "static"),
    { recursive: true },
  );
  cpSync(path.join(stageRoot, "public"), path.join(temporaryOutput, "public"), {
    recursive: true,
  });
  cpSync(path.join(hostRoot, "LICENSE"), path.join(temporaryOutput, "LICENSE"));
  cpSync(path.join(hostRoot, "NOTICE"), path.join(temporaryOutput, "NOTICE"));
  if (!existsSync(path.join(temporaryOutput, "server.js"))) {
    throw new Error("The assembled frontend has no server.js. Read the build output and fix the error.");
  }
}

function refuseEscapingSymlinks(root, current = root) {
  for (const entry of readdirSync(current)) {
    const candidate = path.join(current, entry);
    const stat = lstatSync(candidate);
    if (stat.isSymbolicLink()) {
      const target = realpathSync(candidate);
      const relative = path.relative(root, target);
      if (relative.startsWith("..") || path.isAbsolute(relative)) {
        throw new Error(
          "A standalone output symlink points outside the runtime. Remove the symlink.",
        );
      }
    } else if (stat.isDirectory()) {
      refuseEscapingSymlinks(root, candidate);
    }
  }
}

const interruptHandler = () => handleSignal("SIGINT");
const terminationHandler = () => handleSignal("SIGTERM");
process.on("SIGINT", interruptHandler);
process.on("SIGTERM", terminationHandler);
let buildError = null;
try {
  copyHost();
  makePackageManagerShims();
  const env = { ...process.env };
  delete env.NEXT_DIST_DIR;
  env.SKEIN_FRONTEND_EXTENSIONS = extensions.join(",");
  env.SKEIN_FRONTEND_WORKSPACE_ROOT = workplaceRoot;
  env.NEXT_TELEMETRY_DISABLED = "1";
  env.NEXT_IGNORE_INCORRECT_LOCKFILE = "1";
  env.NODE_ENV = "production";
  env.PATH = `${shimRoot}${path.delimiter}${env.PATH ?? ""}`;

  await runNode(path.join(stageRoot, "scripts", "compose-extensions.mjs"), [], env);
  stopIfSignaled();
  await runNode(requireFromHost.resolve("next/dist/bin/next"), ["build", stageRoot], env);
  stopIfSignaled();
  await promoteStandalone();
  refuseEscapingSymlinks(temporaryOutput);
  rmSync(backupOutput, { recursive: true, force: true });
  if (existsSync(finalOutput)) renameSync(finalOutput, backupOutput);
  try {
    renameSync(temporaryOutput, finalOutput);
  } catch (error) {
    if (existsSync(backupOutput)) renameSync(backupOutput, finalOutput);
    throw error;
  }
  rmSync(backupOutput, { recursive: true, force: true });
  await new Promise((resolve) => setImmediate(resolve));
  stopIfSignaled();
  console.log(`Built Skein frontend at ${finalOutput}`);
} catch (error) {
  buildError = error;
} finally {
  rmSync(stageRoot, { recursive: true, force: true });
  rmSync(shimRoot, { recursive: true, force: true });
  rmSync(temporaryOutput, { recursive: true, force: true });
  if (!existsSync(finalOutput) && existsSync(backupOutput)) {
    renameSync(backupOutput, finalOutput);
  } else {
    rmSync(backupOutput, { recursive: true, force: true });
  }
  try {
    rmdirSync(path.join(workplaceRoot, ".skein"));
  } catch (error) {
    if (error?.code !== "ENOENT" && error?.code !== "ENOTEMPTY") throw error;
  }
  process.removeListener("SIGINT", interruptHandler);
  process.removeListener("SIGTERM", terminationHandler);
}
if (receivedSignal) process.kill(process.pid, receivedSignal);
if (buildError) throw buildError;
