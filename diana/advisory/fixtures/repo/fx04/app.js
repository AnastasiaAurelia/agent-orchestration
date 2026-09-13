// A sink with a constant operand: a sink alone is never a finding.
function greet() {
  var el = document.getElementById("greeting");
  el.innerHTML = "<b>Hello</b>";
}

greet();
