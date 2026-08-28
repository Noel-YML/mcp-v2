/**
 * ARIEL webchat's MCP Apps Host (A1) - read-only bridge, no MCP Client in
 * the browser. `webchat/server.py` has ALREADY executed the governed
 * get_performance_digest tool call server-side (with a fresh, function-key
 * -attached X-Ariel-Scope) by the time this module ever runs; this Host
 * only relays the already-executed tool input/result to a sandboxed View
 * iframe. It never calls tools/call, never calls resources/read from the
 * browser, and never holds/sees a scope JWT, function key, or HotelID -
 * those never appear in the `mcp_app` descriptor webchat/server.py sends
 * (see webchat/mcp_app.py).
 */
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";

/**
 * Mounts one Revenue MCP App instance.
 *
 * @param {Object} opts
 * @param {HTMLElement} opts.container - where to insert the iframe.
 * @param {string} opts.templateHtml - the static View template (fetched
 *   server-side by webchat/server.py's /api/mcp-app/revenue-performance
 *   route - never fetched by this module directly from MCP).
 * @param {Object} opts.toolInput - the ACTUAL executed get_performance_digest
 *   arguments (mcp_app.tool_input from /api/chat's response).
 * @param {Object} opts.toolResult - the ACTUAL governed result object
 *   (mcp_app.tool_result) - already data-minimized/scanned server-side.
 * @returns {{teardown: () => Promise<void>}}
 */
export function mountRevenueApp({ container, templateHtml, toolInput, toolResult }) {
  const iframe = document.createElement("iframe");
  // Production sandbox - allow-scripts ONLY. No allow-same-origin,
  // allow-top-navigation, allow-popups, or allow-forms. Empirically proven
  // sufficient (real Chrome, A1 preflight) for the full App/AppBridge
  // handshake, tool input/result delivery, and teardownResource.
  iframe.setAttribute("sandbox", "allow-scripts");
  iframe.setAttribute("title", "Revenue performance");
  iframe.className = "mcp-app-frame";
  container.appendChild(iframe);

  const transport = new PostMessageTransport(iframe.contentWindow, iframe.contentWindow);
  // client=null: NO MCP Client in the browser. This Host never forwards
  // tools/call or resources/read to any server - see AppBridge's own
  // constructor (`if (this._client) {...}` in connect() is simply never
  // entered), which is why no serverTool capability is ever advertised.
  const bridge = new AppBridge(null, { name: "ariel-webchat-host", version: "1.0.0" }, {}, {});

  bridge.addEventListener("sizechange", ({ height }) => {
    if (typeof height === "number" && height > 0) {
      iframe.style.height = height + "px";
    }
  });

  let torndown = false;

  const initializedPromise = new Promise((resolve) => {
    bridge.addEventListener("initialized", resolve);
  });

  // Connect (start listening) BEFORE navigating the iframe via srcdoc - the
  // View's inline script runs synchronously during parsing and sends its
  // single, unretried ui/initialize request as soon as it loads, which is
  // BEFORE the iframe's own 'load' event fires. Listening first, navigating
  // second, avoids losing that request to a host that isn't listening yet
  // (a real race found and fixed during the A1 real-browser preflight).
  bridge.connect(transport).catch(() => {
    // Connection failed - the ordinary textual chat answer already
    // rendered regardless (see app.js); the app simply never appears.
  });
  iframe.srcdoc = templateHtml;

  initializedPromise.then(async () => {
    await bridge.sendToolInput({ arguments: toolInput });
    await bridge.sendToolResult({
      content: [{ type: "text", text: JSON.stringify(toolResult) }],
      structuredContent: toolResult,
    });
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    // webchat has no in-app theme toggle today - this forwards the one real
    // existing signal (the OS/browser's own color-scheme preference) rather
    // than inventing a second theme source of truth.
    await bridge.sendHostContextChange({ hostContext: { theme: prefersDark ? "dark" : "light" } });
  });

  async function teardown() {
    if (torndown) return;
    torndown = true;
    try {
      await bridge.teardownResource({});
    } catch {
      // Best-effort - still remove the iframe below regardless.
    }
    iframe.remove();
  }

  return { teardown };
}

window.ArielMcpAppHost = { mountRevenueApp };
