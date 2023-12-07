function timer() {
    'use strict';

    var currentPath = window.location.pathname;

    fetch('/ajax?path=' + currentPath)
        .then(response => response.json())
        .then(data => {
            for (const [key, value] of Object.entries(data)) {
                $("#" + key).html(value);
            }
        });
}

$(document).ready(function () {
    timer();
    setInterval(timer, 1000);
});