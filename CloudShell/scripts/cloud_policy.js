// AZP CloudShell page policy: distinguishes automated CDP inputs from physical user inputs.
// Invoked by the native shell via Runtime.evaluate:
//   __azpShellExpect(x, y, dx, dy)  register expected mouse event coords (and relative delta) before dispatch;
//                                   called with no args before keydown
//   __azpShellSetForeground(bool)   update whether the shell window is currently in the foreground
(() => {
    // Keep page visibility active even when minimized so cloud SDK heartbeats don't pause
    for (const [name, value] of [["hidden", false], ["visibilityState", "visible"]]) {
        Object.defineProperty(Document.prototype, name, {configurable: true, get: () => value});
    }

    // Optional WebGL2 disable (gated behind --disable-webgl2; default OFF)
    if (window.__azpShellDisableWebGL2) {
        const getContext = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (type, ...args) {
            return type === "webgl2" ? null : getContext.call(this, type, ...args);
        };
    }

    const kExpectTtlMs = 1000;
    const expected = [];
    let foreground = true;
    let lastAutomatedAt = -Infinity;
    let lastPhysicalPressAt = -Infinity;

    window.__azpShellExpect = (x, y, dx = 0, dy = 0) => {
        lastAutomatedAt = performance.now();
        if (x === undefined) return;
        expected.push({x, y, dx, dy, time: lastAutomatedAt});
        if (expected.length > 64) expected.shift();
    };
    window.__azpShellSetForeground = (value) => {
        foreground = !!value;
    };

    function findExpected(event) {
        while (expected.length && event.timeStamp - expected[0].time > kExpectTtlMs) expected.shift();
        return expected.findLast((item) => item.x === event.clientX && item.y === event.clientY);
    }

    // Physical clicks always pass through; physical hover/wheel events are suppressed while backgrounded
    const kHoverTypes = [
        "mousemove", "mouseover", "mouseout", "mouseenter", "mouseleave", "wheel", "mousewheel",
        "pointermove", "pointerrawupdate", "pointerover", "pointerout", "pointerenter", "pointerleave",
    ];
    const kMoveTypes = new Set(["mousemove", "pointermove", "pointerrawupdate"]);
    for (const type of [...kHoverTypes, "mousedown"]) {
        window.addEventListener(
            type,
            (event) => {
                if (!event.isTrusted) return;
                const entry = findExpected(event);
                if (type === "mousedown") {
                    if (!entry) lastPhysicalPressAt = performance.now();
                    return;
                }

                if (!entry) {
                    if (!foreground) {
                        event.stopImmediatePropagation();
                        if (event.cancelable) event.preventDefault();
                    }
                    return;
                }

                // Apply registered relative camera delta to both pointer and mouse events from the same dispatch
                if (kMoveTypes.has(type) && (entry.dx || entry.dy)) {
                    entry.dispatchTimeStamp ??= event.timeStamp;
                    if (entry.dispatchTimeStamp === event.timeStamp) {
                        Object.defineProperties(event, {movementX: {value: entry.dx}, movementY: {value: entry.dy}});
                        return;
                    }
                }

                // Zero out movementX/Y on non-dragging automated moves so pre-click positioning won't jerk the camera
                if (kMoveTypes.has(type) && !event.buttons) {
                    Object.defineProperties(event, {movementX: {value: 0}, movementY: {value: 0}});
                }
            },
            {capture: true, passive: false},
        );
    }

    // Allow pointer lock only when the latest physical click is newer than the latest automated input.
    // Keep property writable for strict-mode SDKs that re-assign e.requestPointerLock.
    const requestPointerLock = Element.prototype.requestPointerLock;
    Element.prototype.requestPointerLock = function (...args) {
        if (lastAutomatedAt >= lastPhysicalPressAt) {
            return Promise.reject(new DOMException("Pointer lock is not available after automated input.", "NotAllowedError"));
        }
        return requestPointerLock.apply(this, args);
    };

    function findPrimaryMediaTarget() {
        const playLayer = document.getElementById("gamePlayLayer");
        const welinkVideo = document.getElementById("WelinkGameVideo");
        if (welinkVideo && welinkVideo.readyState >= 2 && !welinkVideo.paused) {
            return playLayer || welinkVideo;
        }
        if (playLayer) {
            const rect = playLayer.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                return playLayer;
            }
        }

        let best = null;
        let bestArea = 0;
        for (const el of document.querySelectorAll("video, canvas")) {
            const style = window.getComputedStyle(el);
            if (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity) === 0) {
                continue;
            }
            const rect = el.getBoundingClientRect();
            const area = rect.width * rect.height;
            if (area > bestArea) {
                bestArea = area;
                best = el;
            }
        }
        return best;
    }

    // Some cloud SDKs stop forwarding keyboard events after cursor leaves or window blurs until mouseenter fires
    window.addEventListener(
        "keydown",
        (event) => {
            if (!event.isTrusted) return;
            const target = findPrimaryMediaTarget();
            if (target) {
                target.dispatchEvent(new MouseEvent("mouseenter", {view: window}));
            }
        },
        true,
    );
})();
