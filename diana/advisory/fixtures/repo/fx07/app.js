// Same flow as fx01, routed through the one sanitizer Diana recognizes.
function greet() {
  var el = document.getElementById("greeting");
  el.innerHTML = DOMPurify.sanitize(location.hash);
}

greet();
