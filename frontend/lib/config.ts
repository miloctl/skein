/** Where the API lives. Its own module so lib/api.ts and lib/auth.ts can both
 *  read it without importing each other: api() needs a bearer token from auth,
 *  and auth talks to the API, which is a cycle if either owns this value. */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
