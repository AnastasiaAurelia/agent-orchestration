// Tiny notes page. Renders the fragment as a greeting banner.
function greet() {
  var el = document.getElementById("greeting");
  if (!el) {
    return;
  }
  el.innerHTML = location.hash;
}

window.addEventListener("hashchange", greet);
greet();
