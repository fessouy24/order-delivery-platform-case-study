package com.opsdemo.operations;

import com.sun.net.httpserver.HttpServer;
import org.json.JSONObject;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

/** Standalone wire-level checks using dummy credentials and an ephemeral loopback server. */
public final class ApiClientContractTest {
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    public static void main(String[] args)throws Exception{
        HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
        String origin="http://localhost:"+server.getAddress().getPort();
        AtomicReference<Throwable> failure=new AtomicReference<>();
        AtomicReference<String> csrf=new AtomicReference<>("initial-token");
        server.createContext("/api/v1/",exchange->{
            int status=200;String response="{}";
            try{
                String path=exchange.getRequestURI().getPath();
                String cookie=String.valueOf(exchange.getRequestHeaders().getFirst("Cookie"));
                check(origin.equals(exchange.getRequestHeaders().getFirst("Origin")),"Origin must be the configured server");
                if(path.endsWith("/csrf/")){
                    exchange.getResponseHeaders().add("Set-Cookie","csrftoken="+csrf.get()+"; Path=/; SameSite=Lax");
                    response=new JSONObject().put("csrf_token",csrf.get()).toString();
                }else if(path.endsWith("/login/")){
                    check(cookie.contains("csrftoken"),"Login must include CSRF cookie");
                    check(csrf.get().equals(exchange.getRequestHeaders().getFirst("X-CSRFToken")),"Login must include CSRF header");
                    JSONObject body=new JSONObject(new String(exchange.getRequestBody().readAllBytes(),StandardCharsets.UTF_8));
                    check(body.getString("username").equals("dummy-courier"),"Username must be JSON encoded");
                    exchange.getResponseHeaders().add("Set-Cookie","sessionid=dummy-session; HttpOnly; Path=/; SameSite=Lax");
                    csrf.set("rotated-after-login");
                    exchange.getResponseHeaders().add("Set-Cookie","csrftoken="+csrf.get()+"; Path=/; SameSite=Lax");
                    response="{\"id\":\"dummy-id\",\"roles\":[\"Courier\"]}";
                }else if(path.endsWith("/deliver/")){
                    check(cookie.contains("dummy-session"),"Commands must retain HttpOnly session cookie");
                    check(csrf.get().equals(exchange.getRequestHeaders().getFirst("X-CSRFToken")),"Commands must fetch rotated CSRF token");
                    JSONObject body=new JSONObject(new String(exchange.getRequestBody().readAllBytes(),StandardCharsets.UTF_8));
                    check(body.getInt("version")==3&&body.getString("collected").equals("200.00"),"Commands must preserve version and decimal amount");
                    response="{\"state\":\"DELIVERED\"}";
                }else if(path.endsWith("/orders/")){
                    check(cookie.contains("dummy-session"),"Order reads must include authenticated cookie");response="{\"results\":[],\"next\":null}";
                }else if(path.endsWith("/error/")){status=400;response="{\"detail\":\"Refresh this order before retrying.\"}";}
                else if(path.endsWith("/redirect/")){status=302;exchange.getResponseHeaders().add("Location","https://example.invalid/api/v1/orders/");}
                else if(path.endsWith("/logout/")){exchange.getResponseHeaders().add("Set-Cookie","sessionid=; Path=/; Max-Age=0");response="{\"status\":\"signed_out\"}";}
                else status=404;
            }catch(Throwable error){failure.set(error);status=500;response="{\"detail\":\"Wire assertion failed\"}";}
            byte[] bytes=response.getBytes(StandardCharsets.UTF_8);exchange.getResponseHeaders().add("Content-Type","application/json");exchange.sendResponseHeaders(status,bytes.length);exchange.getResponseBody().write(bytes);exchange.close();
        });
        server.start();
        try{
            ApiClient api=new ApiClient(origin);
            JSONObject profile=api.post("/api/v1/auth/login/",new JSONObject().put("username","dummy-courier").put("password","dummy-test-only"));
            check(profile.getJSONArray("roles").getString(0).equals("Courier"),"Profile must parse");
            check(api.get("/api/v1/orders/").getJSONArray("results").length()==0,"Orders must parse");
            check(api.post("/api/v1/orders/dummy/deliver/",new JSONObject().put("version",3).put("collected","200.00")).getString("state").equals("DELIVERED"),"Command result must parse");
            try{api.endpoint("https://example.invalid/api/v1/orders/");throw new AssertionError("Foreign pagination URL accepted");}catch(IllegalArgumentException expected){}
            try{api.endpoint("/admin/");throw new AssertionError("Non-API URL accepted");}catch(IllegalArgumentException expected){}
            try{api.get("/api/v1/error/");throw new AssertionError("HTTP error ignored");}catch(ApiClient.ApiException expected){check(expected.status==400&&expected.getMessage().contains("Refresh"),"Server errors must remain actionable");}
            try{api.get("/api/v1/redirect/");throw new AssertionError("Redirect followed");}catch(ApiClient.ApiException expected){check(expected.status==302,"Redirects must not leak session credentials");}
            api.post("/api/v1/auth/logout/",new JSONObject());api.clear();check(api.cookieSnapshot().isEmpty(),"Local logout must clear cookies");
            if(failure.get()!=null)throw new AssertionError("Wire assertion failed",failure.get());
            System.out.println("PASS: JSON login, CSRF rotation, HttpOnly session, orders, decimal/version command, origin scope, redirect rejection, error feedback, logout.");
        }finally{server.stop(0);}
    }
}
