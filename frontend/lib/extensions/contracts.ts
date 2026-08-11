import metadata from "../../package.json";

export { FRONTEND_EXTENSION_API } from "@skein/extension-api";
export type {
  FrontendExtension,
  FrontendExtensionRegistry,
} from "@skein/extension-api";

export const SKEIN_FRONTEND_VERSION = metadata.version;
