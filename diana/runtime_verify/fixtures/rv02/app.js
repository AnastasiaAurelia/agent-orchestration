// Same opener-supplied value, written through a text sink instead.
function greet() {
  var el = document.getElementById("greeting");
  if (!el) {
    return;
  }
  el.textContent = window.name;
}

greet();
