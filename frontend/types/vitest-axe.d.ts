import "vitest";
import type { AxeMatchers } from "vitest-axe/matchers";

declare module "vitest" {
  // module augmentation: extending the vitest Assertion interface is the
  // documented way to register matchers, and it is inherently "empty"
  /* eslint-disable-next-line @typescript-eslint/no-empty-object-type */
  interface Assertion extends AxeMatchers {}
  /* eslint-disable-next-line @typescript-eslint/no-empty-object-type */
  interface AsymmetricMatchersContaining extends AxeMatchers {}
}
