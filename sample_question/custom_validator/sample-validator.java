import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;

public class Main {

    private static final int AC = 0;
    private static final int WA = 1;
    private static final int TLE = 2;
    private static final int RE = 3;
    private static final int PE = 4;

    private static BufferedReader input;

    private static void finish(int exitcode, String message) {
        if (message != null) {
            System.err.println(message);
            System.err.flush();
        }
        System.exit(exitcode);
    }

    private static String readLine() throws IOException {
        String line = input.readLine();
        return line == null ? null : line.trim();
    }

    private static Long parseLong(String text) {
        try {
            return Long.valueOf(text.trim());
        } catch (NumberFormatException exception) {
            return null;
        }
    }

    private static String envNumber(String name) {
        String value = System.getenv(name);
        return value == null ? "null" : value;
    }

    private static void printLimits() {
        String userLanguage = System.getenv("USER_LANGUAGE");
        String perLanguageLimits = System.getenv("PER_LANGUAGE_LIMITS");

        System.err.println("{"
                + "\"problem_time_limit\": " + envNumber("PROBLEM_TIME_LIMIT") + ", "
                + "\"problem_output_limit\": " + envNumber("PROBLEM_OUTPUT_LIMIT") + ", "
                + "\"problem_memory_limit\": " + envNumber("PROBLEM_MEMORY_LIMIT") + ", "
                + "\"problem_pid_limit\": " + envNumber("PROBLEM_PID_LIMIT") + ", "
                + "\"user_language\": " + (userLanguage == null ? "null" : "\"" + userLanguage + "\"") + ", "
                + "\"per_language_limits\": " + (perLanguageLimits == null ? "null" : perLanguageLimits)
                + "}");
        System.err.flush();
    }

    private static long readSecretValue() throws IOException {
        String line = readLine();
        Long value = line == null ? null : parseLong(line);
        if (value == null) {
            finish(RE, "Invalid secret data");
        }
        return value;
    }

    public static void main(String[] args) throws IOException {
        input = new BufferedReader(new InputStreamReader(System.in));

        long maxValue = readSecretValue();
        long maxTries = readSecretValue();
        long secret = readSecretValue();

        printLimits();

        System.out.println(maxValue);
        System.out.flush();

        for (long iteration = 0; ; iteration++) {
            String line = readLine();
            if (line == null) {
                break;
            }

            if (line.startsWith("!")) {
                Long answer = parseLong(line.substring(1));
                if (answer == null) {
                    finish(RE, "Invalid data");
                }
                if (answer == secret) {
                    finish(AC, null);
                }
                finish(WA, "!" + secret);
            }

            if (iteration > maxTries) {
                finish(TLE, "Exceeded number of allowed iterations");
            }
            Long value = parseLong(line);
            if (value == null) {
                finish(RE, "Invalid data");
            }
            System.out.println(secret < value ? "<" : ">=");
            System.out.flush();
        }

        finish(RE, "Unexpected flow");
    }
}
