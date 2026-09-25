// The name rule services/api_keys.py (_SAFE_NAME) applies before a name goes
// into a mint command, then the quoting its notice uses (shlex.quote). That
// pair is safe in bash, sh and PowerShell. No one quoting is safe in both for
// every name: PowerShell reads a quote inside a name as the end of the string
// and runs what follows it. Any other name gets null, and the caller shows
// no command for it.
const SAFE_NAME = /^[\p{L}\p{N}_][\p{L}\p{N}_ .@+-]{0,63}$/u;
const BARE = /^[\w@%+=:,./-]+$/;

export function commandName(name: string, suffix = ""): string | null {
  if (!SAFE_NAME.test(name)) return null;
  const word = `${name}${suffix}`;
  return BARE.test(word) ? word : `'${word}'`;
}
