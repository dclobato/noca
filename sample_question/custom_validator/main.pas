program BinarySearch;

uses SysUtils;

var
    lower, upper, number: Integer;
    answer: string;

begin
    ReadLn(upper);
    lower := 1;

    while lower < upper do
    begin
        number := lower + (upper - lower) div 2 + 1;
        WriteLn(number);
        Flush(Output);

        ReadLn(answer);
        answer := Trim(answer);

        if answer = '<' then
            upper := number - 1
        else if answer = '>=' then
            lower := number
        else
        begin
            WriteLn(StdErr, 'Invalid answer from validator: ', answer);
            Halt(1);
        end;
    end;

    WriteLn('!', lower);
    Flush(Output);
    Halt(0);
end.
