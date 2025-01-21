/* eslint-disable import/no-extraneous-dependencies, no-console */
const { find } = require("lodash");
const { execSync } = require("child_process");
const { get, post } = require("request").defaults({ jar: true });
const { seedData } = require("./seed-data");
const fs = require("fs");
var Cookie = require("request-cookies").Cookie;

let cypressConfigBaseUrl;
try {
  const cypressConfig = JSON.parse(fs.readFileSync("cypress.json"));
  cypressConfigBaseUrl = cypressConfig.baseUrl;
} catch (e) {}

const baseUrl = process.env.CYPRESS_baseUrl || cypressConfigBaseUrl || "http://localhost:5001";

function seedDatabase(seedValues) {
  get(baseUrl + "/login", (_, { headers }) => {
    const request = seedValues.shift();
    const data = request.type === "form" ? { formData: request.data } : { json: request.data };

    if (headers["set-cookie"]) {
      const cookies = headers["set-cookie"].map(cookie => new Cookie(cookie));
      const csrfCookie = find(cookies, { key: "csrf_token" });
      if (csrfCookie) {
        if (request.type === "form") {
          data["formData"] = { ...data["formData"], csrf_token: csrfCookie.value };
        } else {
          data["headers"] = { "X-CSRFToken": csrfCookie.value };
        }
      }
    }

    post(baseUrl + request.route, data, (err, response) => {
      const result = response ? response.statusCode : err;
      console.log("POST " + request.route + " - " + result);
      if (seedValues.length) {
        seedDatabase(seedValues);
      }
    });
  });
}

function buildServer() {
  console.log("Building the server...");
  execSync("docker compose -p cypress build", { stdio: "inherit" });
}

function startServer() {
  console.log("Creating the database...");
  execSync("docker compose -p cypress run server create_db", { stdio: "inherit" });
  console.log("Starting the server...");
  execSync("docker compose -p cypress up -d", { stdio: "inherit" });
}

function stopServer() {
  console.log("Stopping the server...");
  execSync("docker compose -p cypress down", { stdio: "inherit" });
}

function runCypressCI() {
  const {
    GITHUB_REPOSITORY,
    CYPRESS_OPTIONS, // eslint-disable-line no-unused-vars
  } = process.env;

  if (GITHUB_REPOSITORY === "getredash/redash") {
    process.env.CYPRESS_OPTIONS = "--record";
  }

  execSync(
    "COMMIT_INFO_MESSAGE=$(git show -s --format=%s) docker compose run --name cypress cypress ./node_modules/.bin/percy exec -t 300 -- ./node_modules/.bin/cypress run $CYPRESS_OPTIONS",
    { stdio: "inherit" }
  );
}

const command = process.argv[2] || "all";

switch (command) {
  case "build":
    buildServer();
    break;
  case "start":
    startServer();
    if (!process.argv.includes("--skip-db-seed")) {
      seedDatabase(seedData);
    }
    break;
  case "db-seed":
    seedDatabase(seedData);
    break;
  case "run":
    execSync("cypress run", { stdio: "inherit" });
    break;
  case "open":
    execSync("cypress open", { stdio: "inherit" });
    break;
  case "run-ci":
    runCypressCI();
    break;
  case "stop":
    stopServer();
    break;
  case "all":
    startServer();
    seedDatabase(seedData);
    execSync("cypress run", { stdio: "inherit" });
    stopServer();
    break;
  default:
    console.log("Usage: yarn cypress [build|start|db-seed|open|run|stop]");
    break;
}
