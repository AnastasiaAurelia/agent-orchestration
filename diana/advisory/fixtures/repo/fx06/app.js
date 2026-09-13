// A lint helper that talks ABOUT the dangerous pattern without performing it.
var RULES = [
  "el.innerHTML = location.hash",
  'document.write(location.search)'
];

function describe() {
  console.log("banned: el.innerHTML = location.hash");
  return RULES.join("\n");
}

describe();
