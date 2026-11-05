package ye.legal.ai;

import android.annotation.SuppressLint;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.app.Activity;

/**
 * Native shell around the Yemeni legal platform.
 *
 * The platform is a server-rendered application (FastAPI) whose own service
 * worker already provides the offline shell, so the native layer stays thin: a
 * WebView that loads the platform, keeps its session cookies, opens external
 * links in the system browser, and shows an Arabic retry screen when the host
 * cannot be reached. Keeping the logic server-side means one deployment serves
 * web, PWA and the app alike.
 */
public class MainActivity extends Activity {

    /** The deployed platform. Override at build time with -PplatformUrl=... */
    private static final String PLATFORM_URL =
            BuildConfig.PLATFORM_URL;

    private WebView webView;
    private View offlineView;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.webView);
        offlineView = findViewById(R.id.offlineView);
        Button retry = findViewById(R.id.retryButton);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                // Anything outside the platform origin opens in the browser so
                // the WebView never becomes an uncontrolled browser.
                if (!uri.toString().startsWith(PLATFORM_URL)) {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    return true;
                }
                return false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                offlineView.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    offlineView.setVisibility(View.VISIBLE);
                }
            }
        });

        retry.setOnClickListener(v -> {
            offlineView.setVisibility(View.GONE);
            webView.reload();
        });

        if (savedInstanceState == null) {
            webView.loadUrl(PLATFORM_URL);
        } else {
            webView.restoreState(savedInstanceState);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
