// Historical note, kept for context:
function greet() {
  var el = document.getElementById("greeting");
  // el.innerHTML = location.hash;
  /* also tried:
     el.innerHTML = location.search;
  */
  el.textContent = "hello";
}

greet();
