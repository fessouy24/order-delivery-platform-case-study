package com.opsdemo.operations;

import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.CookieManager;
import java.net.CookiePolicy;
import java.net.HttpCookie;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

/** Transport cookies stay in memory; optional encrypted session persistence is owned by SessionStore. */
final class ApiClient {
    final URI origin;
    private final CookieManager cookies = new CookieManager(null, CookiePolicy.ACCEPT_ORIGINAL_SERVER);
    ApiClient(String address) {
        origin = URI.create(address);
        if (origin.getHost() == null || origin.getUserInfo() != null || origin.getQuery() != null || origin.getFragment() != null
            || !(origin.getPath().isEmpty() || origin.getPath().equals("/"))
            || !(origin.getScheme().equals("https") || (BuildConfig.DEBUG && origin.getScheme().equals("http") && origin.getHost().equals("localhost"))))
            throw new IllegalArgumentException("Use the configured company HTTPS server.");
    }
    boolean sameOrigin(URI uri) {
        return origin.getScheme().equalsIgnoreCase(uri.getScheme()) && origin.getHost().equalsIgnoreCase(uri.getHost()) && port(origin) == port(uri);
    }
    private int port(URI uri) { return uri.getPort() >= 0 ? uri.getPort() : (uri.getScheme().equals("https") ? 443 : 80); }
    URI endpoint(String path) {
        URI uri = origin.resolve(path);
        if (!sameOrigin(uri) || !uri.getPath().startsWith("/api/v1/")) throw new IllegalArgumentException("Invalid server endpoint.");
        return uri;
    }
    JSONObject get(String path) throws Exception { return new JSONObject(new String(request("GET", path, null, null), StandardCharsets.UTF_8)); }
    JSONObject post(String path, JSONObject data) throws Exception {
        String csrf = get("/api/v1/auth/csrf/").getString("csrf_token");
        return new JSONObject(new String(request("POST", path, data.toString(), csrf), StandardCharsets.UTF_8));
    }
    byte[] photo(String path) throws Exception { return request("GET", path, null, null); }
    void clear() { synchronized (cookies) { cookies.getCookieStore().removeAll(); } }
    void restoreSession(String token,long expiresAt){
        if(!token.matches("[A-Za-z0-9]+"))throw new IllegalArgumentException("Invalid stored session");
        HttpCookie cookie=new HttpCookie("sessionid",token);cookie.setVersion(0);cookie.setPath("/");cookie.setHttpOnly(true);cookie.setSecure(origin.getScheme().equals("https"));cookie.setMaxAge(Math.max(0,(expiresAt-System.currentTimeMillis())/1000));
        synchronized(cookies){cookies.getCookieStore().add(origin,cookie);}
    }
    List<HttpCookie> cookieSnapshot() { synchronized (cookies) { return new java.util.ArrayList<>(cookies.getCookieStore().getCookies()); } }
    void adoptWebCookies(String header) {
        if (header == null) return;
        synchronized (cookies) {
            for (String entry : header.split(";")) {
                int split = entry.indexOf('=');
                if (split < 1) continue;
                HttpCookie cookie = new HttpCookie(entry.substring(0, split).trim(), entry.substring(split + 1).trim());
                cookie.setPath("/"); cookie.setSecure(origin.getScheme().equals("https"));
                cookies.getCookieStore().add(origin, cookie);
            }
        }
    }
    private byte[] request(String method, String path, String body, String csrf) throws Exception {
        URI uri = endpoint(path);
        HttpURLConnection connection = (HttpURLConnection) uri.toURL().openConnection();
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(10000); connection.setReadTimeout(20000);
        connection.setRequestMethod(method);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("Origin", origin.toString());
        connection.setRequestProperty("Referer", origin + "/");
        synchronized (cookies) {
            for (Map.Entry<String,List<String>> entry : cookies.get(uri, java.util.Collections.emptyMap()).entrySet())
                connection.setRequestProperty(entry.getKey(), String.join("; ", entry.getValue()));
        }
        if (csrf != null) connection.setRequestProperty("X-CSRFToken", csrf);
        try {
            if (body != null) {
                connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json");
                try (var output = connection.getOutputStream()) { output.write(body.getBytes(StandardCharsets.UTF_8)); }
            }
            int status = connection.getResponseCode();
            synchronized (cookies) { cookies.put(uri, connection.getHeaderFields()); }
            InputStream stream = status < 400 ? connection.getInputStream() : connection.getErrorStream();
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            if (stream != null) try (stream) {
                byte[] buffer = new byte[8192]; int count;
                while ((count = stream.read(buffer)) != -1) {
                    if (output.size() + count > 5 * 1024 * 1024) throw new IllegalStateException("Server response is too large.");
                    output.write(buffer, 0, count);
                }
            }
            byte[] result = output.toByteArray();
            if (status < 200 || status >= 300) {
                String message = new String(result, StandardCharsets.UTF_8);
                try { JSONObject error = new JSONObject(message); message = error.optString("detail", error.toString()); } catch (Exception ignored) { message = "Server request failed (" + status + ")."; }
                throw new ApiException(status, message);
            }
            return result;
        } finally { connection.disconnect(); }
    }
    static final class ApiException extends Exception {
        final int status;
        ApiException(int status, String message) { super(message); this.status = status; }
    }
}
