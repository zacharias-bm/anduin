from __future__ import annotations
"""Native macOS window hosting a WKWebView pointed at the local HTTP server."""
import objc
import AppKit
import WebKit


class _AppDelegate(AppKit.NSObject):
    """Handles dock icon click to reopen the window."""

    def initWithWindow_(self, anduin_window):
        self = objc.super(_AppDelegate, self).init()
        if self is None:
            return None
        self._anduin_window = anduin_window
        return self

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible):
        if not has_visible:
            self._anduin_window.open()
        return True


class AnduinWindow:
    def __init__(self, port: int):
        self._port = port
        self._window = None
        self._webview = None
        self._app_delegate = None
        self._build()

    def _build(self):
        frame = AppKit.NSMakeRect(200, 200, 1000, 700)
        style = (
            AppKit.NSTitledWindowMask
            | AppKit.NSClosableWindowMask
            | AppKit.NSResizableWindowMask
            | AppKit.NSMiniaturizableWindowMask
        )
        self._window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, AppKit.NSBackingStoreBuffered, False
        )
        self._window.setTitle_("Anduin")
        self._window.setReleasedWhenClosed_(False)
        self._window.setMinSize_(AppKit.NSMakeSize(600, 400))
        self._window.setTitlebarAppearsTransparent_(True)
        self._window.setTitleVisibility_(AppKit.NSTitleVisibility.hidden if hasattr(AppKit, "NSTitleVisibility") else 1)

        config = WebKit.WKWebViewConfiguration.alloc().init()
        self._webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            self._window.contentView().bounds(), config
        )
        self._webview.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        self._window.contentView().addSubview_(self._webview)

        url = AppKit.NSURL.URLWithString_(f"http://127.0.0.1:{self._port}/")
        self._webview.loadRequest_(AppKit.NSURLRequest.requestWithURL_(url))

        self._app_delegate = _AppDelegate.alloc().initWithWindow_(self)
        AppKit.NSApp.setDelegate_(self._app_delegate)

    def open(self):
        # Ensure the app can take focus (regular app instead of accessory)
        policy = AppKit.NSApplicationActivationPolicyRegular
        AppKit.NSApp.setActivationPolicy_(policy)
        
        self._window.makeKeyAndOrderFront_(None)
        self._window.makeFirstResponder_(self._webview)
        AppKit.NSApp.activateIgnoringOtherApps_(True)

    def close(self):
        self._window.orderOut_(None)

    def evaluate_js(self, js: str):
        """Run JavaScript in the webview. Fire-and-forget."""
        if self._webview:
            self._webview.evaluateJavaScript_completionHandler_(js, None)

    @property
    def is_visible(self) -> bool:
        return self._window.isVisible()
