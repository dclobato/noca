'use strict';

const readline = require('readline');

const rl = readline.createInterface({ input: process.stdin, terminal: false });
const iter = rl[Symbol.asyncIterator]();

async function readLine() {
    const result = await iter.next();
    return result.done ? '' : result.value.trim();
}

async function main() {
    let upper = parseInt(await readLine(), 10);
    let lower = 1;

    while (lower < upper) {
        const number = lower + Math.floor((upper - lower) / 2) + 1;
        process.stdout.write(number + '\n');

        const answer = await readLine();

        if (answer === '<') {
            upper = number - 1;
        } else if (answer === '>=') {
            lower = number;
        } else {
            process.stderr.write('Invalid answer from validator: ' + JSON.stringify(answer) + '\n');
            process.exit(1);
        }
    }

    process.stdout.write('!' + lower + '\n');
    process.exit(0);
}

main().catch(err => {
    process.stderr.write(err.message + '\n');
    process.exit(1);
});
