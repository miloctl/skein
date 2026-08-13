import {
  FRONTEND_EXTENSION_API,
  SKEIN_FRONTEND_VERSION,
  type FrontendExtension,
  type FrontendExtensionRegistry,
} from "./contracts";

const IDENTIFIER = /^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/;
const VERSION = /^\d+\.\d+\.\d+$/;
// The same bound the backend applies to one capability request. A composed
// registry above it could never resolve its own decisions.
const MAX_POLICY_ACTIONS = 64;

function tuple(value: string): [number, number, number] {
  if (!VERSION.test(value)) throw new Error(`Invalid extension version: ${value}`);
  return value.split(".").map(Number) as [number, number, number];
}

function compare(
  left: [number, number, number],
  right: [number, number, number],
) {
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return left[index] - right[index];
  }
  return 0;
}

// TypeScript declarations do not validate an installed JavaScript package.
// A malformed manifest must fail HERE with the extension named — not later
// as a bare TypeError inside the shell (Nav calls activePaths.includes on
// every render).
function requireString(owner: string, field: string, value: unknown): string {
  if (typeof value !== "string" || !value.trim())
    throw new Error(`${owner} ${field} must be a non-empty string`);
  return value;
}

function requireAppPath(owner: string, field: string, value: unknown): string {
  const path = requireString(owner, field, value);
  if (!path.startsWith("/") || path.startsWith("//"))
    throw new Error(`${owner} ${field} must be an application-relative path`);
  return path;
}

function requireArray(owner: string, field: string, value: unknown): unknown[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new Error(`${owner} ${field} must be an array`);
  return value;
}

function requireRecord(owner: string, value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error(`${owner} must be an object manifest`);
  return value as Record<string, unknown>;
}

export function registerFrontendExtensions(
  extensions: readonly FrontendExtension[],
): FrontendExtensionRegistry {
  const extensionIds = new Set<string>();
  const contributionIds = new Set<string>();
  const navigation: FrontendExtensionRegistry["navigation"][number][] = [];
  const dashboardCards: FrontendExtensionRegistry["dashboardCards"][number][] = [];
  const actions = new Set<string>();
  const core = tuple(SKEIN_FRONTEND_VERSION);

  for (const extension of extensions) {
    const manifest = requireRecord("A frontend extension", extension);
    const id = requireString("A frontend extension", "id", manifest.id);
    if (!IDENTIFIER.test(id)) throw new Error(`Invalid frontend extension id: ${id}`);
    if (extensionIds.has(id)) throw new Error(`Duplicate frontend extension id: ${id}`);
    extensionIds.add(id);
    tuple(requireString(id, "version", manifest.version));
    if (manifest.extensionApi !== FRONTEND_EXTENSION_API)
      throw new Error(
        `${id} needs frontend extension API ${String(manifest.extensionApi)}`,
      );
    const minimum = tuple(requireString(id, "minimumCore", manifest.minimumCore));
    const maximum = tuple(
      requireString(id, "maximumCoreExclusive", manifest.maximumCoreExclusive),
    );
    if (compare(core, minimum) < 0 || compare(core, maximum) >= 0)
      throw new Error(`${id} does not support this Skein version`);

    for (const raw of requireArray(id, "navigation", manifest.navigation)) {
      const contribution = requireRecord(`${id} navigation`, raw);
      const contributionId = registerContribution(
        id,
        requireString(id, "navigation id", contribution.id),
        contributionIds,
      );
      requireString(contributionId, "label", contribution.label);
      requireAppPath(contributionId, "href", contribution.href);
      for (const activePath of requireArray(
        contributionId,
        "activePaths",
        contribution.activePaths,
      ))
        requireAppPath(contributionId, "activePaths entry", activePath);
      registerAction(contributionId, contribution.policyAction, actions);
      navigation.push({
        id: contributionId,
        href: contribution.href as string,
        label: contribution.label as string,
        activePaths: (contribution.activePaths ?? []) as string[],
        policyAction: contribution.policyAction as string | undefined,
        extensionId: id,
      });
    }
    for (const raw of requireArray(id, "dashboardCards", manifest.dashboardCards)) {
      const contribution = requireRecord(`${id} dashboardCards`, raw);
      const contributionId = registerContribution(
        id,
        requireString(id, "dashboard card id", contribution.id),
        contributionIds,
      );
      if (contribution.slot !== "manager-dashboard")
        throw new Error(`${contributionId} slot must be "manager-dashboard"`);
      if (typeof contribution.component !== "function")
        throw new Error(`${contributionId} component must be a React component`);
      registerAction(contributionId, contribution.policyAction, actions);
      dashboardCards.push({
        id: contributionId,
        slot: "manager-dashboard",
        component:
          contribution.component as FrontendExtensionRegistry["dashboardCards"][number]["component"],
        policyAction: contribution.policyAction as string | undefined,
        extensionId: id,
      });
    }
  }
  if (actions.size > MAX_POLICY_ACTIONS)
    throw new Error(
      `The composed extensions declare ${actions.size} policy actions; the capability request supports at most ${MAX_POLICY_ACTIONS}`,
    );
  return Object.freeze({
    extensions: Object.freeze([...extensions]),
    navigation: Object.freeze(navigation),
    dashboardCards: Object.freeze(dashboardCards),
    policyActions: Object.freeze([...actions].sort()),
  });
}

function registerContribution(
  extensionId: string,
  id: string,
  seen: Set<string>,
): string {
  if (!IDENTIFIER.test(id) || !id.startsWith(`${extensionId}.`))
    throw new Error(`${id} must use the ${extensionId} namespace`);
  if (seen.has(id)) throw new Error(`Duplicate frontend contribution id: ${id}`);
  seen.add(id);
  return id;
}

function registerAction(owner: string, value: unknown, actions: Set<string>) {
  if (value === undefined) return;
  const action = requireString(owner, "policyAction", value);
  if (action.length > 160) throw new Error(`${owner} policyAction is too long`);
  actions.add(action);
}
