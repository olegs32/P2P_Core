def web_ajax_add(page, agent, update_tag='data'):
    html = """
    <script type="text/javascript">
function timer() {
    'use strict';
    var currentPath = window.location.pathname;

    $.ajax({
        url: '/ajax' + currentPath,
        cache: false,
        success: function (html) {
            if (html && html.json) {
                for (const [key, value] of Object.entries(html.json)) {
                    $("#" + key).html(value);
                }
            }
        },
        error: function (xhr, status, error) {
            console.error("AJAX request failed:", status, error);
        }
    });
}

$(document).ready(function () {
    timer();
    setInterval(timer, 1000);
});
    </script>"""
    return html


"""<script type="text/javascript">function timer(){
                        'use strict';
                        var currentPath = window.location.pathname;
                        $.ajax({url: '/ajax/cicd/main_page?agent=common', cache: false, success: function(html){
                        
                        for (const [key, value] of Object.entries(html.json)) {
                        $("#" + key).html(value);}
                        }});
                        }$(document).ready(function(){timer();
                        setInterval('timer()',1000);});
                        </script>"""


"""

fetch('/ajax?path=123')
  .then(response => response.json())
  .then(data => {
    // 'data' is already parsed JSON object
    console.log(data.deploy_agents);
  })
  .catch(error => console.error('Error fetching data:', error));
"""