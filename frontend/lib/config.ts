/** Both session bootstrap and the API client need this value. Keeping it here
 *  prevents a circular import between lib/auth.ts and lib/api.ts. */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Auth bootstrap and ordinary API failures describe the same unreachable server.
export const backendUnreachable = (error?: unknown) =>
  `Cannot reach the backend at ${API_URL}. Check that the server is running, then try again.` +
  (error ? ` (${error instanceof Error ? error.message : String(error)})` : "");
