// The quoting services/api_keys.py applies with shlex.quote, so the command
// Settings prints matches the one in the key-request notice. A name reaches
// these commands unvalidated: "ava lee" split into a key for "ava" labelled
// "lee". A leading "@" is quoted too, because PowerShell expands it.
export function shellQuote(word: string): string {
  if (/^\w[\w@%+=:,./-]*$/.test(word)) return word;
  return `'${word.replace(/'/g, `'"'"'`)}'`;
}
