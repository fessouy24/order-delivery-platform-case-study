package com.opsdemo.operations;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DatePickerDialog;
import android.app.DownloadManager;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.Environment;
import android.text.InputType;
import android.util.LruCache;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.net.HttpCookie;
import java.net.URI;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;

/** Native courier screens; other employees retain the server's role-scoped workspace. */
public class MainActivity extends Activity {
    private ApiClient api;
    private SessionStore sessionStore;
    private boolean rememberSession=true, restoringSession;
    private JSONObject account, selected;
    private final List<JSONObject> orders = new ArrayList<>();
    private JSONObject cashBalance=new JSONObject();
    private final ExecutorService requests = Executors.newSingleThreadExecutor();
    private final ExecutorService images = Executors.newFixedThreadPool(2);
    private final LruCache<String,Bitmap> photos = new LruCache<>(8 * 1024 * 1024) {
        protected int sizeOf(String key, Bitmap value) { return value.getByteCount(); }
    };
    private LinearLayout root, body, toolbar;
    private TextView message;
    private ProgressBar progress;
    private WebView staff;
    private boolean arabic, busy, historyScreen;
    private boolean visibleActivity;
    private void syncLocation(){
        boolean active=false;for(JSONObject order:orders)if(order.optString("state").equals("OUT_FOR_DELIVERY"))active=true;
        if(!active||account==null||!courierOnly(account)){stopService(new Intent(this,DeliveryLocationService.class));return;}
        if(visibleActivity&&checkSelfPermission(android.Manifest.permission.ACCESS_COARSE_LOCATION)==android.content.pm.PackageManager.PERMISSION_GRANTED){
            DeliveryLocationService.transport=api;
            try{startForegroundService(new Intent(this,DeliveryLocationService.class));}catch(RuntimeException unavailable){info(tr("Open the app to enable delivery location.","افتح التطبيق لتفعيل موقع التوصيل."));}
        }
    }
    private final Handler refreshHandler = new Handler(Looper.getMainLooper());
    private final Runnable refreshTick = new Runnable() { public void run() {
        if(account!=null && staff==null && selected==null && !historyScreen && !busy) refreshOrders();
        refreshHandler.postDelayed(this,30000);
    }};
    private int generation;
    private ValueCallback<Uri[]> fileChoice;
    private static final int RED = 0xffb42318, GREEN = 0xff166534, YELLOW = 0xfffacc15;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        arabic = getPreferences(0).getBoolean("arabic", true);
        if(Build.VERSION.SDK_INT>=33) getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,this::handleBack);
        try { api = new ApiClient(BuildConfig.SERVER_URL); sessionStore=new SessionStore(this,api.origin.toString()); restoreSignIn(); }
        catch (Exception error) { TextView text = new TextView(this); text.setText("The company server has not been configured for this build."); text.setPadding(32,64,32,32); setContentView(text); }
    }
    private String tr(String en, String ar) { return arabic ? ar : en; }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView text(String value, int size) {
        TextView text = new TextView(this); text.setText(value); text.setTextSize(size); text.setTextColor(0xff232323); text.setPadding(0,dp(8),0,dp(8)); return text;
    }
    private Button button(String title, int color, Runnable action) {
        Button button = new Button(this); button.setText(title); button.setAllCaps(false); button.setTextSize(15); button.setMinHeight(dp(52));
        button.setTextColor(color == YELLOW || color == Color.WHITE ? Color.BLACK : Color.WHITE);
        GradientDrawable background = new GradientDrawable(); background.setColor(color); background.setCornerRadius(dp(10));
        button.setBackground(background);
        LinearLayout.LayoutParams layout = new LinearLayout.LayoutParams(-1,-2); layout.setMargins(0,dp(6),0,dp(6)); button.setLayoutParams(layout);
        button.setOnClickListener(view -> { if (!busy) action.run(); }); return button;
    }
    private EditText field(String hint, int type) {
        EditText edit = new EditText(this); edit.setHint(hint); edit.setContentDescription(hint); edit.setInputType(type); edit.setSingleLine(true); edit.setMinHeight(dp(52));
        body.addView(text(hint,14)); body.addView(edit); return edit;
    }
    private void shell(String title, boolean back, boolean language) {
        generation++;
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(0xfff5f5f5);
        root.setLayoutDirection(arabic ? View.LAYOUT_DIRECTION_RTL : View.LAYOUT_DIRECTION_LTR);
        root.setOnApplyWindowInsetsListener((view,insets) -> { view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom()); return insets; });
        toolbar = new LinearLayout(this); toolbar.setGravity(Gravity.CENTER_VERTICAL); toolbar.setPadding(dp(12),dp(4),dp(12),dp(4)); toolbar.setBackgroundColor(Color.WHITE);
        if (back) { Button backButton = button(tr("Back","رجوع"), Color.WHITE, () -> onBackPressed()); toolbar.addView(backButton,new LinearLayout.LayoutParams(-2,-2)); }
        ImageView companyLogo=new ImageView(this);companyLogo.setImageResource(R.drawable.company_logo);companyLogo.setContentDescription("OPSDEMO Coffee Roasters");companyLogo.setScaleType(ImageView.ScaleType.FIT_CENTER);toolbar.addView(companyLogo,new LinearLayout.LayoutParams(dp(46),dp(46)));
        TextView brand = text(title,18); brand.setTypeface(null,Typeface.BOLD); toolbar.addView(brand,new LinearLayout.LayoutParams(0,-2,1));
        if (language) toolbar.addView(button(arabic ? "English" : "العربية", Color.WHITE, () -> {
            arabic = !arabic; getPreferences(0).edit().putBoolean("arabic",arabic).apply();
            if (account == null) showLogin(); else if (selected != null) showDetails(selected); else showOrders();
        }),new LinearLayout.LayoutParams(-2,-2));
        root.addView(toolbar);
        progress = new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal); progress.setIndeterminate(true); progress.setVisibility(busy ? View.VISIBLE : View.GONE); root.addView(progress,new LinearLayout.LayoutParams(-1,dp(3)));
        message = text("",14); message.setTextColor(RED); message.setPadding(dp(16),dp(8),dp(16),dp(8)); message.setVisibility(View.GONE); root.addView(message);
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true);
        body = new LinearLayout(this); body.setOrientation(LinearLayout.VERTICAL); body.setPadding(dp(16),dp(12),dp(16),dp(20));
        scroll.addView(body); root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1)); setContentView(root); root.requestApplyInsets();
    }
    private void info(String value) { message.setText(value); message.setVisibility(View.VISIBLE); }
    private void setBusy(boolean value) { busy=value; if(progress!=null) progress.setVisibility(value?View.VISIBLE:View.GONE); }
    private interface Task<T> { T run() throws Exception; }
    private interface Success<T> { void accept(T value) throws Exception; }
    private <T> void run(Task<T> task, Success<T> success) {
        if(busy) return; setBusy(true);
        requests.submit(() -> {
            try {
                T result=task.run(); runOnUiThread(() -> { if(isDestroyed())return; setBusy(false); try { success.accept(result); } catch(Exception e) { info(tr("Could not display the server response.","تعذر عرض رد النظام.")); } });
            } catch(Exception error) { runOnUiThread(() -> { if(isDestroyed())return; setBusy(false);
                if(account!=null&&error instanceof ApiClient.ApiException&&(((ApiClient.ApiException)error).status==401||((ApiClient.ApiException)error).status==403)){clearSession();info(tr("Please sign in again.","يرجى تسجيل الدخول مرة أخرى."));return;}
                String detail=error instanceof ApiClient.ApiException ? error.getMessage() : tr("Connection interrupted. Refresh before retrying an order update.","انقطع الاتصال. حدّث الطلب قبل إعادة محاولة التحديث.");
                info(detail);
            }); }
        });
    }
    private void restoreSignIn(){
        JSONObject saved=sessionStore.load();
        if(saved==null){showLogin();return;}
        try{api.restoreSession(saved.getString("token"),saved.getLong("expires"));}catch(Exception error){sessionStore.clear();showLogin();return;}
        rememberSession=true;restoringSession=true;
        shell("OPSDEMO",false,false);body.addView(text(tr("Connecting to your account…","جارٍ الاتصال بحسابك…"),22));
        body.addView(button(tr("Retry connection","إعادة الاتصال"),Color.BLACK,this::restoreSignIn));
        body.addView(button(tr("Use another account","استخدام حساب آخر"),Color.WHITE,this::clearSession));
        run(()->{try{return api.get("/api/v1/auth/me/");}catch(ApiClient.ApiException error){if(error.status==401||error.status==403)return new JSONObject().put("session_expired",true);throw error;}},profile->{
            if(profile.optBoolean("session_expired")||!courierOnly(profile)){clearSession();info(tr("Please sign in again.","يرجى تسجيل الدخول مرة أخرى."));}
            else signedIn(profile);
        });
    }
    private boolean courierOnly(JSONObject profile){JSONArray roles=profile.optJSONArray("roles");return roles!=null&&roles.length()==1&&roles.optString(0).equals("Courier");}
    private void showLogin() {
        stopService(new Intent(this,DeliveryLocationService.class));DeliveryLocationService.transport=null;
        sessionStore.clear();restoringSession=false;
        account=null; selected=null; staff=null; orders.clear(); cashBalance=new JSONObject(); photos.evictAll(); api.clear();
        shell("OPSDEMO",false,true);
        body.addView(text(tr("Sign in","تسجيل الدخول"),26));
        if(BuildConfig.DEBUG) body.addView(text(tr("Trial app · USB connection required","نسخة تجريبية · يلزم اتصال USB"),14));
        EditText username=field(tr("Username","اسم المستخدم"),InputType.TYPE_CLASS_TEXT);
        EditText password=field(tr("Password","كلمة المرور"),InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        password.setSaveEnabled(false);
        CheckBox remember=new CheckBox(this);remember.setText(tr("Keep me signed in","إبقائي مسجلاً للدخول"));remember.setChecked(rememberSession);remember.setMinHeight(dp(48));remember.setOnCheckedChangeListener((view,checked)->rememberSession=checked);body.addView(remember);
        body.addView(text(tr("Couriers: stay signed in for up to 30 days on this phone.","للمندوبين: يظل حسابك مسجلاً على هذا الهاتف لمدة تصل إلى ٣٠ يومًا."),14));
        body.addView(button(tr("Sign in","دخول"),Color.BLACK,()-> {
            String name=username.getText().toString().trim(), secret=password.getText().toString();
            if(name.isEmpty()||secret.isEmpty()){ info(tr("Enter username and password.","أدخل اسم المستخدم وكلمة المرور."));return; }
            password.setText("");
            run(()->api.post("/api/v1/auth/login/",new JSONObject().put("username",name).put("password",secret).put("remember_me",rememberSession)),profile->{
                if(profile.optBoolean("mfa_required")) showMfa(profile.optBoolean("enrollment_required")); else signedIn(profile);
            });
        }));
    }
    private void showMfa(boolean enrollment) {
        shell("OPSDEMO",false,true);
        if(enrollment) { body.addView(text(tr("Set up your authenticator in the company website first, then sign in here.","أكمل إعداد تطبيق المصادقة على موقع الشركة أولاً، ثم سجّل الدخول هنا."),18)); body.addView(button(tr("Back to sign in","العودة للدخول"),Color.BLACK,this::showLogin)); return; }
        EditText code=field(tr("Six-digit code","رمز من ستة أرقام"),InputType.TYPE_CLASS_NUMBER);
        body.addView(button(tr("Confirm","تأكيد"),Color.BLACK,()->{String value=code.getText().toString();run(()->api.post("/api/v1/auth/mfa/verify/",new JSONObject().put("token",value)),this::signedIn);}));
    }
    private void signedIn(JSONObject profile) throws Exception {
        boolean storageFailed=false;
        if(!restoringSession){
            sessionStore.clear();
            if(rememberSession&&courierOnly(profile)){
                try{boolean saved=false;for(HttpCookie cookie:api.cookieSnapshot())if(cookie.getName().equals("sessionid")&&!cookie.hasExpired()&&cookie.getMaxAge()>0){sessionStore.save(cookie.getValue(),System.currentTimeMillis()+Math.min(cookie.getMaxAge(),30L*86400)*1000);saved=true;break;}if(!saved)throw new IllegalStateException("No persistent session");}
                catch(Exception error){sessionStore.clear();storageFailed=true;}
            }
        }
        restoringSession=false;
        account=profile; selected=null;cashBalance=new JSONObject();
        JSONArray roles=profile.getJSONArray("roles"); boolean courier=false,other=false;
        for(int i=0;i<roles.length();i++){ if(roles.getString(i).equals("Courier"))courier=true;else other=true; }
        if(courier&&!other){ showOrders(); refreshOrders(); } else showStaff();
        if(storageFailed)info(tr("Signed in, but this phone could not remember the session. You may need to sign in next time.","تم الدخول، لكن تعذر حفظ الجلسة على هذا الهاتف. قد تحتاج للدخول مرة أخرى."));
    }
    private void showOrders() {
        selected=null; historyScreen=false; shell(tr("My deliveries","متجر تجريبيي")+" · "+cashLabel(),false,true);
        toolbar.addView(button(tr("Sign out","خروج"),Color.WHITE,this::signOut),new LinearLayout.LayoutParams(-2,-2));
        body.addView(button(tr("Refresh","تحديث"),Color.WHITE,this::refreshOrders));
        boolean dispatched=false;for(JSONObject order:orders)if(order.optString("state").equals("OUT_FOR_DELIVERY"))dispatched=true;
        if(dispatched){
            body.addView(text(tr("Delivery location is shared with operations and management only while orders are out for delivery. It stops after the last delivery, cancellation or postponement.","يُشارك موقع التوصيل مع الحركة والإدارة أثناء خروج المتجر تجريبي فقط، ويتوقف بعد آخر تسليم أو إلغاء أو تأجيل."),14));
            body.addView(button(tr("Enable delivery location","تفعيل موقع التوصيل"),Color.WHITE,()->{
                if(checkSelfPermission(android.Manifest.permission.ACCESS_COARSE_LOCATION)!=android.content.pm.PackageManager.PERMISSION_GRANTED)
                    requestPermissions(Build.VERSION.SDK_INT>=33?new String[]{android.Manifest.permission.ACCESS_FINE_LOCATION,android.Manifest.permission.ACCESS_COARSE_LOCATION,android.Manifest.permission.POST_NOTIFICATIONS}:new String[]{android.Manifest.permission.ACCESS_FINE_LOCATION,android.Manifest.permission.ACCESS_COARSE_LOCATION},91);
                else{syncLocation();info(tr("Delivery location enabled. Keep GPS on.","تم تفعيل موقع التوصيل. أبق تحديد الموقع مفعلاً."));}
            }));
        }
        syncLocation();
        int active=0;
        for(JSONObject order:orders) if(!closed(order)){ active++; orderCard(order); }
        if(active==0)body.addView(text(tr("No assigned deliveries.","لا توجد متجر تجريبي مسندة إليك."),18));
        List<JSONObject> history=new ArrayList<>(); for(JSONObject order:orders)if(closed(order))history.add(order);
        if(!history.isEmpty())body.addView(button(tr("Completed / cancelled","المكتمل والملغي"),Color.WHITE,()->{
            historyScreen=true; shell(tr("Order history","المتجر تجريبي السابقة"),true,true); for(JSONObject order:history)orderCard(order);
        }));
    }
    private boolean closed(JSONObject order){ return order.optString("state").equals("DELIVERED")||order.optString("state").equals("CANCELLED"); }
    private void statusIcon(TextView view,String state){
        int icon=state.equals("DELIVERED")?R.drawable.status_delivered:state.equals("CANCELLED")?R.drawable.status_cancelled:R.drawable.status_calendar;
        view.setCompoundDrawablesRelativeWithIntrinsicBounds(icon,0,0,0);view.setCompoundDrawablePadding(dp(8));
    }
    private void orderCard(JSONObject order){
        Button card=button(courierLabel(order),Color.WHITE,()->{ selected=order;showDetails(order); });card.setGravity(Gravity.START|Gravity.CENTER_VERTICAL);card.setPadding(dp(16),dp(16),dp(16),dp(16));body.addView(card);
    }
    private String courierLabel(JSONObject order){
        return order.optString(arabic?"courier_label_ar":"courier_label",order.optString("customer_name"));
    }
    private String status(String value){
        switch(value){
            case "SCHEDULED":return tr("Scheduled","مجدول");case "READY":return tr("Ready","جاهز");case "ASSIGNED":return tr("Assigned","مسند للمندوب");case "OUT_FOR_DELIVERY":return tr("Out for delivery","خرج للتسليم");case "DELIVERED":return tr("Delivered","تم التسليم");case "CANCELLED":return tr("Cancelled","ملغي");default:return tr("Preparing","قيد التجهيز");
        }
    }
    private String cashLabel(){
        StringBuilder label=new StringBuilder();java.util.Iterator<String> keys=cashBalance.keys();
        while(keys.hasNext()){String currency=keys.next();if(label.length()>0)label.append(" · ");label.append(cashBalance.optString(currency)).append(" ").append(currency);}
        return label.length()==0?"0.00 EGP":label.toString();
    }
    private List<JSONObject> readOrders() throws Exception {
        List<JSONObject> result=new ArrayList<>();String next="/api/v1/orders/";
        for(int page=0;next!=null&&page<1000;page++){
            JSONObject response=api.get(next);JSONArray rows=response.getJSONArray("results");for(int i=0;i<rows.length();i++)result.add(rows.getJSONObject(i));
            next=response.isNull("next")?null:response.getString("next");
        }
        if(next!=null)throw new IllegalStateException("Too many orders. Contact operations.");cashBalance=api.get("/api/v1/courier/cash/").getJSONObject("balance");return result;
    }
    private void refreshOrders(){ run(this::readOrders,rows->{orders.clear();orders.addAll(rows);showOrders();}); }
    private void showDetails(JSONObject order){
        selected=order;shell(tr("Order details","تفاصيل الطلب"),true,true);
        body.addView(text(courierLabel(order),26));body.addView(text(order.optString("customer_name"),18));body.addView(text(status(order.optString("state")),16));
        statusIcon((TextView)body.getChildAt(body.getChildCount()-1),order.optString("state"));
        detail(tr("Phone","الهاتف"),order.optString("customer_phone"));
        String phone=order.optString("customer_phone").replaceAll("[^+0-9]","");
        if(!phone.isEmpty())body.addView(button(tr("Call customer","اتصال بالعميل"),Color.WHITE,()->external(Uri.parse("tel:"+phone))));
        detail(tr("Address","العنوان"),order.optString("full_address"));
        detail(tr("Area","المنطقة"),order.isNull("area_name")?order.optString("original_area"):order.optString("area_name"));
        detail(tr("Delivery date","موعد التسليم"),order.optString("delivery_date"));
        String navigation=order.optString("navigation_url");
        if(!order.optString("source").equals("SHOPIFY")&&!navigation.isEmpty())body.addView(button(tr("Open Google Maps","فتح خرائط جوجل"),Color.WHITE,()->external(Uri.parse(navigation))));
        if(order.optString("source").equals("SHOPIFY")&&!closed(order)){
            Button whatsapp=button(tr("Send WhatsApp message","إرسال رسالة واتساب"),Color.WHITE,()->run(()->api.post("/api/v1/orders/"+order.optString("id")+"/whatsapp/",new JSONObject()),this::openWhatsApp));
            body.addView(whatsapp);
            if(!order.optString("delivery_date").equals(LocalDate.now(ZoneId.of("Africa/Cairo")).toString()))body.addView(text(tr("The delivery-today message needs today's delivery date. Ask Supply Chain to reschedule this order.","رسالة التوصيل اليوم تحتاج موعد تسليم النهارده. اطلب من مسؤول الحركة تحديث ميعاد الطلب."),14));
        }
        body.addView(text(tr("Ordered items","المنتجات المطلوبة"),22));
        JSONArray lines=order.optJSONArray("lines");
        if(lines!=null)for(int i=0;i<lines.length();i++){
            JSONObject line=lines.optJSONObject(i);if(line==null)continue;
            if(order.optBoolean("stock_issued")&&!line.isNull("sent_quantity")&&line.optDouble("sent_quantity")==0)continue;
            String photo=line.isNull("photo_url")?"":line.optString("photo_url");
            if(!photo.isEmpty()){ImageView image=new ImageView(this);image.setContentDescription(line.optString("name"));image.setScaleType(ImageView.ScaleType.FIT_CENTER);body.addView(image,new LinearLayout.LayoutParams(-1,dp(180)));loadPhoto(image,photo,generation);}
            String quantity=line.optString("quantity");try{quantity=new java.math.BigDecimal(quantity).stripTrailingZeros().toPlainString();}catch(Exception ignored){}
            if(order.optBoolean("stock_issued")&&!line.isNull("sent_quantity"))quantity=line.optString("sent_quantity");
            body.addView(text(line.optString("name")+"\n× "+quantity,18));
        }
        detail(tr("Collect","المطلوب تحصيله"),order.optString("expected_collection","0")+" "+order.optString("currency"));
        if(order.optBoolean("review_required"))body.addView(text(tr("Contact the coordinator before delivery.","تواصل مع المسؤول قبل التسليم."),16));
        if(!closed(order)){
            LinearLayout outcomes=new LinearLayout(this);outcomes.setPadding(dp(8),dp(8),dp(8),dp(8));outcomes.setBackgroundColor(Color.WHITE);
            Button cancel=button(tr("Cancelled","إلغاء"),RED,()->confirm("cancel"));
            Button deliver=button(tr("Delivered","تسليم"),GREEN,()->confirm("deliver"));
            Button postpone=button(tr("Postponed","تأجيل"),YELLOW,()->confirm("postpone"));
            statusIcon(cancel,"CANCELLED");statusIcon(deliver,"DELIVERED");statusIcon(postpone,"SCHEDULED");
            cancel.getCompoundDrawablesRelative()[0].setTint(Color.WHITE);deliver.getCompoundDrawablesRelative()[0].setTint(Color.WHITE);
            deliver.setEnabled(order.optString("state").equals("OUT_FOR_DELIVERY")&&!order.optBoolean("review_required"));
            for(Button action:new Button[]{cancel,deliver,postpone}){LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(64),1);p.setMargins(dp(3),0,dp(3),0);outcomes.addView(action,p);}
            root.addView(outcomes);
            if(order.optString("source").equals("SHOPIFY")){
                Button noAnswer=button(tr("Didn't answer","لم يرد"),0xff1565c0,()->confirm("no-answer"));
                noAnswer.setCompoundDrawablesRelativeWithIntrinsicBounds(R.drawable.status_no_answer,0,0,0);noAnswer.setCompoundDrawablePadding(dp(8));
                noAnswer.setEnabled(order.optString("state").equals("OUT_FOR_DELIVERY"));root.addView(noAnswer);
            }
            if(!deliver.isEnabled())body.addView(text(tr("Delivery is enabled after dispatch is recorded.","تأكيد التسليم متاح بعد تسجيل خروج الطلب."),14));
        }
        body.addView(button(tr("Refresh order","تحديث الطلب"),Color.WHITE,()->run(()->api.get("/api/v1/orders/"+order.optString("id")+"/"),this::showDetails)));
    }
    private void detail(String label,String value){TextView name=text(label,14);name.setTypeface(null,Typeface.BOLD);body.addView(name);body.addView(text(value==null||value.isEmpty()?tr("Not supplied","غير متوفر"):value,18));}
    private void loadPhoto(ImageView image,String path,int screen){
        Bitmap cached=photos.get(path);if(cached!=null){image.setImageBitmap(cached);return;}
        images.submit(()->{try{byte[] data=api.photo(path);BitmapFactory.Options options=new BitmapFactory.Options();options.inSampleSize=2;Bitmap bitmap=BitmapFactory.decodeByteArray(data,0,data.length,options);if(bitmap!=null){photos.put(path,bitmap);runOnUiThread(()->{if(!isDestroyed()&&generation==screen)image.setImageBitmap(bitmap);});}}catch(Exception ignored){runOnUiThread(()->{if(generation==screen)image.setContentDescription(tr("Photo unavailable","الصورة غير متاحة"));});}});
    }
    private void confirm(String action){
        JSONObject order=selected;if(order==null)return;
        if(action.equals("no-answer")){
            String message=order.optInt("no_answer_count")>0?tr("This is the second unanswered delivery attempt. Confirming will cancel this order and notify Admin.","هذه محاولة التوصيل الثانية دون رد. التأكيد سيلغي الطلب ويخطر مسؤول النظام."):tr("Admin will be notified and this order will move to Upcoming on the next company working day.","سيتم إخطار مسؤول النظام ونقل الطلب إلى القادم في يوم العمل التالي للشركة.");
            new AlertDialog.Builder(this).setTitle(tr("Confirm unanswered delivery","تأكيد عدم الرد")).setMessage(message).setNegativeButton(tr("Go back","رجوع"),null).setPositiveButton(tr("Confirm","تأكيد"),(dialog,which)->run(()->{api.post("/api/v1/orders/"+order.getString("id")+"/no-answer/",new JSONObject().put("version",order.getInt("version")));return readOrders();},rows->{orders.clear();orders.addAll(rows);showOrders();info(tr("Unanswered delivery recorded. Admin notified.","تم تسجيل عدم الرد وإخطار مسؤول النظام."));})).show();return;
        }
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(dp(20),dp(12),dp(20),dp(8));form.setLayoutDirection(arabic?View.LAYOUT_DIRECTION_RTL:View.LAYOUT_DIRECTION_LTR);
        EditText amount=new EditText(this);Spinner reason=new Spinner(this);final String[] chosenDate={LocalDate.now(ZoneId.of("Africa/Cairo")).plusDays(1).toString()};
        if(action.equals("deliver")){form.addView(text(tr("Collected amount","المبلغ المحصل")+" ("+order.optString("currency")+")",16));amount.setInputType(InputType.TYPE_CLASS_NUMBER|InputType.TYPE_NUMBER_FLAG_DECIMAL);amount.setText(order.optString("expected_collection","0"));form.addView(amount);}
        if(action.equals("postpone")){
            try{LocalDate due=LocalDate.parse(order.getString("delivery_date")).plusDays(1);if(due.isAfter(LocalDate.parse(chosenDate[0])))chosenDate[0]=due.toString();}catch(Exception ignored){}
            Button date=button(tr("Choose date: ","اختر اليوم: ")+chosenDate[0],Color.WHITE,()->{});
            date.setOnClickListener(view->{LocalDate current=LocalDate.parse(chosenDate[0]);DatePickerDialog picker=new DatePickerDialog(this,(calendar,y,m,d)->{chosenDate[0]=LocalDate.of(y,m+1,d).toString();date.setText(tr("New date: ","الموعد الجديد: ")+chosenDate[0]);},current.getYear(),current.getMonthValue()-1,current.getDayOfMonth());picker.show();});
            form.addView(date);
        }
        String[] values=action.equals("cancel")?new String[]{"Customer cancelled","Customer refused order","Incorrect order","Other — contact coordinator"}:new String[]{"Customer requested another day","Customer unavailable","Delivery could not be completed"};
        String[] labels=action.equals("cancel")?new String[]{tr(values[0],"إلغاء بطلب العميل"),tr(values[1],"رفض العميل الاستلام"),tr(values[2],"الطلب غير صحيح"),tr(values[3],"سبب آخر — تواصل مع المسؤول")}:new String[]{tr(values[0],"طلب العميل يومًا آخر"),tr(values[1],"العميل غير متاح"),tr(values[2],"تعذر إكمال التسليم")};
        if(!action.equals("deliver")){form.addView(text(tr("Reason","السبب"),16));reason.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,labels));form.addView(reason);}
        String title=action.equals("deliver")?tr("Confirm delivery","تأكيد التسليم"):action.equals("cancel")?tr("Confirm cancellation","تأكيد الإلغاء"):tr("Postpone delivery","تأجيل التسليم");
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle(title).setView(form).setNegativeButton(tr("Go back","رجوع"),null).setPositiveButton(tr("Confirm","تأكيد"),null).create();dialog.show();
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setTextColor(action.equals("deliver")?GREEN:action.equals("cancel")?RED:0xff7a5900);
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view->{
            try{
                JSONObject data=new JSONObject().put("version",order.getInt("version"));
                if(action.equals("deliver")) {String value=amount.getText().toString();if(!value.matches("[0-9]+(\\.[0-9]{1,2})?")){amount.setError(tr("Enter the amount collected.","أدخل المبلغ المحصل."));return;}data.put("collected",value);}
                else data.put("reason",values[reason.getSelectedItemPosition()]);
                if(action.equals("postpone"))data.put("delivery_date",chosenDate[0]);
                dialog.dismiss();
                run(()->{api.post("/api/v1/orders/"+order.getString("id")+"/"+action+"/",data);return readOrders();},rows->{orders.clear();orders.addAll(rows);showOrders();info(tr("Saved.","تم الحفظ."));});
            }catch(Exception error){info(tr("Check the order and try again.","راجع الطلب ثم أعد المحاولة."));}
        });
    }
    private void openWhatsApp(JSONObject result) {
        String phone=result.optString("phone"),message=result.optString("message");
        if(!phone.matches("[1-9][0-9]{7,14}")||message.isEmpty()){info(tr("Customer phone or message is missing.","رقم العميل أو الرسالة غير موجود."));return;}
        Uri link=Uri.parse("https://api.whatsapp.com/send").buildUpon().appendQueryParameter("phone",phone).appendQueryParameter("text",message).build();
        for(String app:new String[]{"com.whatsapp","com.whatsapp.w4b"}){
            try{startActivity(new Intent(Intent.ACTION_VIEW,link).setPackage(app));return;}catch(android.content.ActivityNotFoundException missing){/* Try the other WhatsApp app or browser. */}
        }
        external(Uri.parse(result.optString("url")));
    }
    private void external(Uri uri){
        String scheme=uri.getScheme();
        if(!"https".equals(scheme)&&!"tel".equals(scheme))return;
        try{startActivity(new Intent("tel".equals(scheme)?Intent.ACTION_DIAL:Intent.ACTION_VIEW,uri));}catch(Exception error){info(tr("No app is available to open this link.","لا يوجد تطبيق مناسب لفتح الرابط."));}
    }
    @SuppressWarnings("SetJavaScriptEnabled")
    private void showStaff(){
        selected=null;shell("OPSDEMO",false,false);
        toolbar.addView(button(tr("Sign out","خروج"),Color.WHITE,this::signOut),new LinearLayout.LayoutParams(-2,-2));
        root.removeViewAt(root.getChildCount()-1);
        staff=new WebView(this);staff.getSettings().setJavaScriptEnabled(true);staff.getSettings().setDomStorageEnabled(true);
        staff.getSettings().setAllowFileAccess(false);staff.getSettings().setAllowContentAccess(false);staff.getSettings().setMixedContentMode(android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        CookieManager webCookies=CookieManager.getInstance();webCookies.setAcceptCookie(true);webCookies.setAcceptThirdPartyCookies(staff,false);
        webCookies.removeAllCookies(done->setStaffCookies(api.cookieSnapshot(),0));
        staff.setWebViewClient(new WebViewClient(){
            public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){try{if(api.sameOrigin(URI.create(request.getUrl().toString())))return false;}catch(Exception ignored){}if(request.isForMainFrame())external(request.getUrl());return true;}
            public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){if(request.isForMainFrame()){info(tr("Connection lost. Tap Refresh to retry.","انقطع الاتصال. اضغط تحديث للمحاولة."));}}
        });
        staff.setWebChromeClient(new WebChromeClient(){
            public boolean onShowFileChooser(WebView web,ValueCallback<Uri[]> callback,FileChooserParams params){if(fileChoice!=null)fileChoice.onReceiveValue(null);fileChoice=callback;Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT);intent.setType("image/*");intent.addCategory(Intent.CATEGORY_OPENABLE);try{startActivityForResult(intent,7);}catch(Exception e){callback.onReceiveValue(null);fileChoice=null;}return true;}
        });
        staff.setDownloadListener((url,agent,disposition,mime,length)->{
            try{URI target=URI.create(url);if(!api.sameOrigin(target))return;DownloadManager.Request request=new DownloadManager.Request(Uri.parse(url));String cookie=webCookies.getCookie(api.origin.toString());if(cookie!=null)request.addRequestHeader("Cookie",cookie);String filename=android.webkit.URLUtil.guessFileName(url,disposition,mime);request.setDestinationInExternalFilesDir(this,Environment.DIRECTORY_DOWNLOADS,filename);request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);((DownloadManager)getSystemService(DOWNLOAD_SERVICE)).enqueue(request);info(tr("Download started.","بدأ التنزيل."));}catch(Exception e){info(tr("Could not download the report.","تعذر تنزيل التقرير."));}
        });
        toolbar.addView(button(tr("Refresh","تحديث"),Color.WHITE,()->staff.reload()),new LinearLayout.LayoutParams(-2,-2));root.addView(staff,new LinearLayout.LayoutParams(-1,0,1));
    }
    private void setStaffCookies(List<HttpCookie> cookies,int index){
        if(staff==null||isDestroyed())return;
        if(index==cookies.size()){CookieManager.getInstance().flush();staff.loadUrl(api.origin.toString());return;}
        HttpCookie cookie=cookies.get(index);
        CookieManager.getInstance().setCookie(api.origin.toString(),cookie.getName()+"="+cookie.getValue()+"; Path=/"+(cookie.getSecure()?"; Secure":"")+(cookie.isHttpOnly()?"; HttpOnly":""),done->setStaffCookies(cookies,index+1));
    }
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(request==7&&fileChoice!=null){fileChoice.onReceiveValue(result==RESULT_OK&&data!=null&&data.getData()!=null?new Uri[]{data.getData()}:null);fileChoice=null;}}
    private void signOut(){
        stopService(new Intent(this,DeliveryLocationService.class));
        sessionStore.clear();
        if(staff!=null)api.adoptWebCookies(CookieManager.getInstance().getCookie(api.origin.toString()));
        run(()->{try{api.post("/api/v1/auth/logout/",new JSONObject());return true;}catch(Exception e){return false;}finally{api.clear();}},done->{clearSession();if(!done)info(tr("Signed out on this phone. The server could not be reached.","تم الخروج من الهاتف. تعذر الاتصال بالخادم."));});
    }
    private void clearSession(){sessionStore.clear();CookieManager.getInstance().removeAllCookies(null);CookieManager.getInstance().flush();if(staff!=null){staff.stopLoading();staff.clearCache(true);staff.destroy();staff=null;}showLogin();}
    @Override public void onBackPressed(){
        handleBack();
    }
    private void handleBack(){
        if(busy)return;
        if(staff!=null){staff.evaluateJavascript("(()=>{const e=new Event('opsdemo-native-back',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;})()",consumed->{if(!"true".equals(consumed)){if(staff.canGoBack())staff.goBack();else exitPrompt();}});return;}
        if(account!=null&&selected!=null){showOrders();return;}
        if(account!=null&&historyScreen){showOrders();return;}
        exitPrompt();
    }
    private void exitPrompt(){new AlertDialog.Builder(this).setMessage(tr("Close OPSDEMO?","إغلاق التطبيق؟")).setNegativeButton(tr("Stay","البقاء"),null).setPositiveButton(tr("Close","إغلاق"),(dialog,which)->finish()).show();}
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results){super.onRequestPermissionsResult(request,permissions,results);if(request==91){syncLocation();if(checkSelfPermission(android.Manifest.permission.ACCESS_COARSE_LOCATION)!=android.content.pm.PackageManager.PERMISSION_GRANTED)info(tr("Location permission was not granted. Operations will see no recent location.","لم يُمنح إذن الموقع. ستظهر للحركة عبارة لا يوجد موقع حديث."));}}
    @Override protected void onResume(){super.onResume();visibleActivity=true;syncLocation();refreshHandler.postDelayed(refreshTick,30000);}
    @Override protected void onPause(){visibleActivity=false;refreshHandler.removeCallbacks(refreshTick);super.onPause();}
    @Override protected void onDestroy(){generation++;refreshHandler.removeCallbacks(refreshTick);requests.shutdownNow();images.shutdownNow();if(fileChoice!=null)fileChoice.onReceiveValue(null);if(staff!=null){staff.destroy();staff=null;}photos.evictAll();super.onDestroy();}
}
