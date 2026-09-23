import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["e2e/**/*.ts", "e2e-oidc/**/*.ts"],
    rules: {
      // Playwright 1.62 turns interception off when the last route goes. A
      // request the page starts during that switch stays pending forever: the
      // OIDC walk sat at "Checking your browser session" on a stranded
      // /api/auth/config. Keep routes for the page's life and switch with a flag.
      "no-restricted-syntax": ["error", {
        selector: "CallExpression[callee.property.name=/^unroute(All)?$/]",
        message: "Removing a route can strand in-flight requests. Keep the route and switch its behavior with a flag, then call route.fallback().",
      }],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // e2e artifacts: the separate dist dirs playwright builds into, and its
    // reports (playwright.config.ts, playwright.oidc.config.ts)
    ".next-e2e/**",
    ".next-oidc/**",
    "test-results/**",
    "playwright-report/**",
  ]),
]);

export default eslintConfig;
