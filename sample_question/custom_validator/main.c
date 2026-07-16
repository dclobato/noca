#include <stdio.h>
#include <string.h>
#include <stdlib.h>

int main(void) {
    int upper, lower = 1;
    scanf("%d", &upper);

    while (lower < upper) {
        int number = lower + (upper - lower) / 2 + 1;
        printf("%d\n", number);
        fflush(stdout);

        char answer[16];
        scanf("%15s", answer);

        if (strcmp(answer, "<") == 0) {
            upper = number - 1;
        } else if (strcmp(answer, ">=") == 0) {
            lower = number;
        } else {
            fprintf(stderr, "Invalid answer from validator: %s\n", answer);
            return 1;
        }
    }

    printf("!%d\n", lower);
    fflush(stdout);
    return 0;
}
