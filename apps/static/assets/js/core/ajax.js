function timer() {
    'use strict';
    var currentPath = window.location.pathname;

//    $.ajax({
//        url: '/ajax?path=' + currentPath,
//        cache: false,
//        success: function (html) {
////        const responseObject = JSON.parse(html);
//        JavaScriptSerializer js = new JavaScriptSerializer();
//        dynamic obj = js.DeserializeObject(html);
fetch('/ajax?path=123')
  .then(response => response.json())
  .then(data => {
    // 'data' is already parsed JSON object


                for (const [key, value] of Object.entries(data)) {
                    $("#" + key).html(value);
                }})

//        },
//        error: function (xhr, status, error) {
//            console.error("AJAX request failed:", status, error);
//        }
//    });
}

$(document).ready(function () {
    timer();
    setInterval(timer, 1000);
});