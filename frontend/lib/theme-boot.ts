/** The pre-paint script layout.tsx inlines into <head>.
 *
 *  It runs BEFORE first paint, before React, before any bundle loads, so it
 *  cannot import anything at runtime — it has to be a self-contained string.
 *  That is why the pack ids, colorway ids, storage keys and custom-hue
 *  formulas used to be written out a second time by hand in layout.tsx, where
 *  they drifted from lib/theme.ts twice.
 *
 *  Being unable to IMPORT at runtime does not mean being unable to GENERATE at
 *  build time. Everything below is derived from the exports of lib/theme.ts,
 *  so a new pack, a renamed key or a retuned formula reaches the pre-paint
 *  path without anyone remembering this file exists.
 */

import { SIDEBAR_KEY } from "./chat-layout";
import {
  APPEARANCE_KEY,
  COLORWAYS,
  CUSTOM_KEY,
  CUSTOM_LC,
  DEFAULT_COLORWAY,
  DEFAULT_PACK,
  PACK_KEY,
  PACKS,
  THEME_KEY,
} from "./theme";

// The default id needs no data attribute — globals.css carries it on :root —
// so the script only has to recognize the others. Same rule applyPrefs uses.
const nonDefault = (rows: readonly { id: string }[], fallback: string) =>
  JSON.stringify(rows.map((r) => r.id).filter((id) => id !== fallback));

// One setProperty per custom token, reading the hue from the local var the
// script computed (`th` or `w`) and interpolating it into the same
// light-dark(oklch(...)) shape theme.ts builds at runtime.
const customProps = Object.entries(CUSTOM_LC)
  .map(([token, lc]) => {
    const h = lc.hue === "weld" ? "w" : "th";
    const light = `oklch(${lc.light[0]} ${lc.light[1]} "+${h}+")`;
    const dark = `oklch(${lc.dark[0]} ${lc.dark[1]} "+${h}+")`;
    return `d.style.setProperty(${JSON.stringify(token)},"light-dark(${light}, ${dark})")`;
  })
  .join(";");

/** Self-contained JS for the inline <script> in layout.tsx. */
export function themeBootScript(): string {
  return (
    `(function(){try{var d=document.documentElement,` +
    `t=localStorage.getItem(${JSON.stringify(THEME_KEY)});` +
    `if(${nonDefault(COLORWAYS, DEFAULT_COLORWAY)}.indexOf(t)>=0)d.dataset.theme=t;` +
    `else if(t==="custom"){` +
    `var c=JSON.parse(localStorage.getItem(${JSON.stringify(CUSTOM_KEY)})||"{}"),` +
    `th=((Math.round(+c.thread)%360)+360)%360,` +
    `w=((Math.round(+c.weld)%360)+360)%360;` +
    `if(isFinite(th)&&isFinite(w)){d.dataset.theme="custom";${customProps}}}` +
    `var a=localStorage.getItem(${JSON.stringify(APPEARANCE_KEY)});` +
    `if(a==="light"||a==="dark")d.dataset.appearance=a;` +
    `var p=localStorage.getItem(${JSON.stringify(PACK_KEY)});` +
    `if(${nonDefault(PACKS, DEFAULT_PACK)}.indexOf(p)>=0)d.dataset.pack=p;` +
    `if(localStorage.getItem(${JSON.stringify(SIDEBAR_KEY)})==="1")` +
    `d.dataset.chatSidebar="collapsed";` +
    // Which keyboard the reader has, for the capture shortcut (globals.css
    // hides the wrong spelling, components/shortcut.tsx writes both). It
    // rides in THIS script because the answer must be on the element before
    // first paint -- a second inline script would parse-block again for one
    // attribute, and reading it in React instead would either flash the wrong
    // key or mismatch on hydration. userAgentData first, because
    // navigator.platform is deprecated and lies under some emulation.
    `var pl=(navigator.userAgentData&&navigator.userAgentData.platform)` +
    `||navigator.platform||"";` +
    `if(/Mac|iPhone|iPad|iPod/.test(pl))d.dataset.os="mac"` +
    `}catch(e){}})()`
  );
}
