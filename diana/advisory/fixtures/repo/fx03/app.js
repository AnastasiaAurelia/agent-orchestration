// Same shape as fx01, but assigns through textContent, which does not parse HTML.
function greet() {
  var el = document.getElementById("greeting");
  el.textContent = location.hash;
}

greet();
