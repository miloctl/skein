import {
  FRONTEND_EXTENSION_API,
  SKEIN_FRONTEND_VERSION,
  type FrontendExtension,
  type FrontendExtensionRegistry,
} from "./contracts";

const IDENTIFIER = /^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/;
const VERSION = /^\d+\.\d+\.\d+$/;

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
    if (!IDENTIFIER.test(extension.id))
      throw new Error(`Invalid frontend extension id: ${extension.id}`);
    if (extensionIds.has(extension.id))
      throw new Error(`Duplicate frontend extension id: ${extension.id}`);
    extensionIds.add(extension.id);
    tuple(extension.version);
    if (extension.extensionApi !== FRONTEND_EXTENSION_API)
      throw new Error(
        `${extension.id} needs frontend extension API ${extension.extensionApi}`,
      );
    const minimum = tuple(extension.minimumCore);
    const maximum = tuple(extension.maximumCoreExclusive);
    if (compare(core, minimum) < 0 || compare(core, maximum) >= 0)
      throw new Error(`${extension.id} does not support this Skein version`);

    for (const contribution of extension.navigation ?? []) {
      registerContribution(extension.id, contribution.id, contributionIds);
      if (!contribution.href.startsWith("/") || contribution.href.startsWith("//"))
        throw new Error(`${contribution.id} must use an application-relative href`);
      if (contribution.policyAction) actions.add(contribution.policyAction);
      navigation.push({ ...contribution, extensionId: extension.id });
    }
    for (const contribution of extension.dashboardCards ?? []) {
      registerContribution(extension.id, contribution.id, contributionIds);
      if (contribution.policyAction) actions.add(contribution.policyAction);
      dashboardCards.push({ ...contribution, extensionId: extension.id });
    }
  }
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
) {
  if (!IDENTIFIER.test(id) || !id.startsWith(`${extensionId}.`))
    throw new Error(`${id} must use the ${extensionId} namespace`);
  if (seen.has(id)) throw new Error(`Duplicate frontend contribution id: ${id}`);
  seen.add(id);
}
