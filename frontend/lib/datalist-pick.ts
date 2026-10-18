/** Whether this change event is a datalist suggestion the person picked, as
 *  opposed to a keystroke. A mouse pick fires no Enter, so an input that
 *  commits on Enter must also commit on a pick, or the pick does nothing.
 *  A pick arrives as insertReplacementText, or with no inputType at all in
 *  Firefox. Typing arrives as insertText and must not commit, or every
 *  letter saves a partial name. */
export function isDatalistPick(event: { nativeEvent: Event }): boolean {
  const type = (event.nativeEvent as InputEvent).inputType;
  return !type || type === "insertReplacementText";
}
