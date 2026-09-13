// The same dangerous flow, expressed through a template literal.
function greet() {
  var el = document.getElementById("greeting");
  el.innerHTML = `<span>${location.hash}</span>`;
}

greet();
