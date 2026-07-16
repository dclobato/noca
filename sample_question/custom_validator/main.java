import java.util.Scanner;

class Main {
    public static void main(String[] args) {
        Scanner scanner = new Scanner(System.in);
        int upper = scanner.nextInt();
        int lower = 1;

        while (lower < upper) {
            int number = lower + (upper - lower) / 2 + 1;
            System.out.println(number);
            System.out.flush();

            String answer = scanner.next();

            if (answer.equals("<")) {
                upper = number - 1;
            } else if (answer.equals(">=")) {
                lower = number;
            } else {
                throw new RuntimeException("Invalid answer from validator: " + answer);
            }
        }

        System.out.println("!" + lower);
        System.out.flush();
        System.exit(0);
    }
}
