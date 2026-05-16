"use client";

import { useEffect, useState } from "react";

/**
 * True on coarse-pointer (touch) devices — phones and tablets.
 *
 * #3 mobile: SPACE-centric voice copy ("hold SPACE to talk") reads as
 * "voice is broken" on a phone, which has no spacebar (tap-to-talk DOES
 * work via the mic button's onClick + VAD auto-stop). Components use this
 * to swap to touch-correct copy / hide desktop-only affordances.
 *
 * SSR-safe: returns false during the static-export render and the first
 * client paint, then resolves after mount. `(pointer: coarse)` also
 * covers tablets, unlike a width breakpoint.
 */
export function useIsTouch(): boolean {
  const [isTouch, setIsTouch] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(pointer: coarse)");
    const update = () => setIsTouch(mq.matches);
    update();
    mq.addEventListener?.("change", update);
    return () => mq.removeEventListener?.("change", update);
  }, []);
  return isTouch;
}
