"use strict";

/**
 * Native Tab Switcher
 *
 * Получает команды от Python через Native Messaging.
 *
 * Команды:
 *
 *   {"command": "next_tab"}
 *
 * Переключает Firefox на следующую открытую вкладку.
 * После последней вкладки переходит на первую.
 */

const HOST_NAME = "com.local.native_tab_switcher";

let port = null;

function log(...args) {
  console.log("[NativeTabSwitcher]", ...args);
}

function warn(...args) {
  console.warn("[NativeTabSwitcher]", ...args);
}

function connect() {
  try {
    port = browser.runtime.connectNative(HOST_NAME);
  } catch (err) {
    warn("Failed to start connectNative():", err);
    return;
  }

  port.onMessage.addListener(handleMessage);

  port.onDisconnect.addListener(() => {
    if (browser.runtime.lastError) {
      warn(
        "Native host disconnected with error:",
        browser.runtime.lastError.message
      );
    } else {
      log("Native host disconnected.");
    }

    port = null;
  });

  log(`Connected to native host "${HOST_NAME}".`);
}

function sendResponse(response) {
  if (!port) {
    warn(
      "Cannot send response, port is not connected:",
      response
    );
    return;
  }

  try {
    port.postMessage(response);
  } catch (err) {
    warn("Failed to post message to native host:", err);
  }
}

async function handleMessage(message) {
  log("Received message from native host:", message);

  if (
    !message ||
    typeof message !== "object" ||
    Array.isArray(message)
  ) {
    sendResponse({
      status: "error",
      message: "Malformed message: expected a JSON object.",
    });

    return;
  }

  const { command } = message;

  if (
    command === undefined ||
    command === null ||
    command === ""
  ) {
    sendResponse({
      status: "error",
      message: "Missing 'command' field.",
    });

    return;
  }

  if (command === "next_tab") {
    await handleNextTab();
    return;
  }

  sendResponse({
    status: "error",
    message: `Unknown command: '${command}'.`,
  });
}

async function handleNextTab() {
  let tabs;

  try {
    tabs = await browser.tabs.query({
      currentWindow: true,
    });
  } catch (err) {
    sendResponse({
      status: "error",
      message: `Failed to query tabs: ${err.message}`,
    });

    return;
  }

  if (!tabs || tabs.length === 0) {
    sendResponse({
      status: "error",
      message: "No open tabs.",
    });

    return;
  }

  // Sort explicitly by Firefox tab position.
  tabs.sort((a, b) => a.index - b.index);

  const activeIndex = tabs.findIndex(
    (tab) => tab.active
  );

  if (activeIndex === -1) {
    sendResponse({
      status: "error",
      message: "Could not determine active tab.",
    });

    return;
  }

  // Next tab; after the last one, return to index 0.
  const nextIndex =
    (activeIndex + 1) % tabs.length;

  const currentTab = tabs[activeIndex];
  const targetTab = tabs[nextIndex];

  try {
    await browser.tabs.update(
      targetTab.id,
      {
        active: true,
      }
    );

    await browser.windows.update(
      targetTab.windowId,
      {
        focused: true,
      }
    );
  } catch (err) {
    sendResponse({
      status: "error",
      message: `Failed to activate tab: ${err.message}`,
    });

    return;
  }

  log(
    `Switched tab ${activeIndex} → ${nextIndex} ` +
    `(tabId=${targetTab.id})`
  );

  sendResponse({
    status: "ok",
    command: "next_tab",
    from: activeIndex,
    to: nextIndex,
    total_tabs: tabs.length,
  });
}

connect();
