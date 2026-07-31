//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const assert = require("assert");
const path = require("path");

const keyedRows = require(
  path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-keyed-rows.js"),
);

class Row {
  constructor(teamId) {
    this.attributes = { "data-team-id": String(teamId) };
    this.value = null;
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

class Body {
  constructor() {
    this.children = [];
  }
  appendChild(row) {
    const existing = this.children.indexOf(row);
    if (existing !== -1) {
      this.children.splice(existing, 1);
    }
    this.children.push(row);
  }
  removeChild(row) {
    this.children = this.children.filter((candidate) => candidate !== row);
  }
}

function reconcile(body, teams) {
  return keyedRows.reconcile(body, teams, {
    key: (team) => team.id,
    build: (team) => {
      const row = new Row(team.id);
      row.value = team.value;
      return row;
    },
    update: (row, team) => {
      row.value = team.value;
    },
  });
}

const body = new Body();
assert.strictEqual(
  reconcile(body, [
    { id: "a", value: 1 },
    { id: "b", value: 2 },
    { id: "__proto__", value: 3 },
  ]),
  3,
);
const rowA = body.children[0];
const rowB = body.children[1];
const specialRow = body.children[2];

reconcile(body, [
  { id: "__proto__", value: 30 },
  { id: "b", value: 20 },
]);

assert.deepStrictEqual(body.children, [specialRow, rowB], "server order is authoritative");
assert.strictEqual(body.children[0].value, 30, "special object keys remain safe");
assert.strictEqual(body.children[1].value, 20, "surviving rows update in place");
assert.ok(!body.children.includes(rowA), "departed teams are removed");
console.log("animator-keyed-rows contract: OK");
