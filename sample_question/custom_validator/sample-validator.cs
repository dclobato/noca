using System;
using System.Globalization;

internal static class SampleValidator
{
    private const int AC = 0;
    private const int WA = 1;
    private const int TLE = 2;
    private const int RE = 3;
    private const int PE = 4;

    private static void Finish(int exitcode, string message = null)
    {
        if (!string.IsNullOrEmpty(message))
        {
            Console.Error.WriteLine(message);
            Console.Error.Flush();
        }
        Environment.Exit(exitcode);
    }

    private static string ReadTrimmedLine()
    {
        string line = Console.In.ReadLine();
        return line?.Trim();
    }

    private static long? ParseInteger(string text)
    {
        if (long.TryParse(text?.Trim(), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out long value))
        {
            return value;
        }
        return null;
    }

    private static string EnvNumber(string name)
    {
        return Environment.GetEnvironmentVariable(name) ?? "null";
    }

    private static void PrintLimits()
    {
        string userLanguage = Environment.GetEnvironmentVariable("USER_LANGUAGE");
        string perLanguageLimits = Environment.GetEnvironmentVariable("PER_LANGUAGE_LIMITS");

        Console.Error.WriteLine(
            "{"
            + "\"problem_time_limit\": " + EnvNumber("PROBLEM_TIME_LIMIT") + ", "
            + "\"problem_output_limit\": " + EnvNumber("PROBLEM_OUTPUT_LIMIT") + ", "
            + "\"problem_memory_limit\": " + EnvNumber("PROBLEM_MEMORY_LIMIT") + ", "
            + "\"problem_pid_limit\": " + EnvNumber("PROBLEM_PID_LIMIT") + ", "
            + "\"user_language\": " + (userLanguage == null ? "null" : "\"" + userLanguage + "\"") + ", "
            + "\"per_language_limits\": " + (perLanguageLimits ?? "null")
            + "}");
        Console.Error.Flush();
    }

    private static long ReadSecretValue()
    {
        long? value = ParseInteger(ReadTrimmedLine());
        if (value == null)
        {
            Finish(RE, "Invalid secret data");
        }
        return value.Value;
    }

    private static void Main()
    {
        Console.OutputEncoding = System.Text.Encoding.UTF8;

        long maxValue = ReadSecretValue();
        long maxTries = ReadSecretValue();
        long secret = ReadSecretValue();

        PrintLimits();

        Console.Out.WriteLine(maxValue);
        Console.Out.Flush();

        for (long iteration = 0; ; iteration++)
        {
            string user = ReadTrimmedLine();
            if (user == null)
            {
                break;
            }

            if (user.StartsWith("!", StringComparison.Ordinal))
            {
                long? answer = ParseInteger(user.Substring(1));
                if (answer == null)
                {
                    Finish(RE, "Invalid data");
                }
                if (answer.Value == secret)
                {
                    Finish(AC);
                }
                Finish(WA, "!" + secret.ToString(CultureInfo.InvariantCulture));
            }

            if (iteration > maxTries)
            {
                Finish(TLE, "Exceeded number of allowed iterations");
            }
            long? value = ParseInteger(user);
            if (value == null)
            {
                Finish(RE, "Invalid data");
            }
            Console.Out.WriteLine(secret < value.Value ? "<" : ">=");
            Console.Out.Flush();
        }

        Finish(RE, "Unexpected flow");
    }
}
