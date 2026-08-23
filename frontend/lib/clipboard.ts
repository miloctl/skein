/** navigator.clipboard exists only in secure contexts (https / localhost);
 *  Skein can be served over plain http (no secure context), so fall back to
 *  the legacy hidden-textarea path there. */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through — permission denied or focus lost
    }
  }
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  let ta: HTMLTextAreaElement | null = null;
  try {
    ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    ta?.remove();
    try {
      previous?.focus({ preventScroll: true });
    } catch {}
  }
}
