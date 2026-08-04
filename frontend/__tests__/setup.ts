import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

// RTL auto-cleans only when vitest runs with globals; this config does not, so
// without this every rendered tree stays in the document and getByRole finds
// duplicates from earlier tests
afterEach(cleanup);

// Node 25 ships Web Storage on by default, and its localStorage is a
// non-configurable global that SHADOWS jsdom's: window.localStorage becomes a
// bare object with no getItem, so identity reads return undefined and the
// failure reads like a jsdom bug. package.json runs vitest with
// --no-experimental-webstorage; say so here, because `npx vitest` bypasses it.
if (typeof window.localStorage?.getItem !== "function") {
  throw new Error(
    "window.localStorage is not a Storage. Run tests with `npm test`, which sets" +
      " NODE_OPTIONS=--no-experimental-webstorage so jsdom's Storage is not shadowed" +
      " by the Node built-in.",
  );
}

// localStorage and sessionStorage carry identity (the picked user, the pasted
// API key, the OIDC token) and the once-per-session wave. Left dirty, one
// test signs in the next one.
beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});
