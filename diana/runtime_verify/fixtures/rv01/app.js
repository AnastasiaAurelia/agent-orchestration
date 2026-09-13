// Renders the opener-supplied window name as a greeting banner.
function greet() {
  var el = document.getElementById("greeting");
  if (!el) {
    return;
  }
  el.innerHTML = window.name;
}

greet();
