program SampleValidator;

{$mode objfpc}{$H+}

uses
  SysUtils;

const
  AC = 0;
  WA = 1;
  TLE = 2;
  RE = 3;
  PE = 4;

procedure Finish(ExitCode: Integer; const Message: string);
begin
  if Message <> '' then
  begin
    WriteLn(StdErr, Message);
    Flush(StdErr);
  end;
  Halt(ExitCode);
end;

function ReadTrimmedLine(out Line: string): Boolean;
begin
  if Eof(Input) then
  begin
    Line := '';
    Exit(False);
  end;
  ReadLn(Input, Line);
  Line := Trim(Line);
  Result := True;
end;

function ParseInteger(const Text: string; out Value: Int64): Boolean;
begin
  Result := TryStrToInt64(Trim(Text), Value);
end;

function EnvNumber(const Name: string): string;
begin
  Result := GetEnvironmentVariable(Name);
  if Result = '' then
    Result := 'null';
end;

procedure PrintLimits;
var
  UserLanguage, PerLanguageLimits: string;
begin
  UserLanguage := GetEnvironmentVariable('USER_LANGUAGE');
  if UserLanguage = '' then
    UserLanguage := 'null'
  else
    UserLanguage := '"' + UserLanguage + '"';

  PerLanguageLimits := GetEnvironmentVariable('PER_LANGUAGE_LIMITS');
  if PerLanguageLimits = '' then
    PerLanguageLimits := 'null';

  WriteLn(StdErr,
    '{' +
    '"problem_time_limit": ' + EnvNumber('PROBLEM_TIME_LIMIT') + ', ' +
    '"problem_output_limit": ' + EnvNumber('PROBLEM_OUTPUT_LIMIT') + ', ' +
    '"problem_memory_limit": ' + EnvNumber('PROBLEM_MEMORY_LIMIT') + ', ' +
    '"problem_pid_limit": ' + EnvNumber('PROBLEM_PID_LIMIT') + ', ' +
    '"user_language": ' + UserLanguage + ', ' +
    '"per_language_limits": ' + PerLanguageLimits +
    '}');
  Flush(StdErr);
end;

function ReadSecretValue: Int64;
var
  Line: string;
begin
  if not ReadTrimmedLine(Line) then
    Finish(RE, 'Invalid secret data');
  if not ParseInteger(Line, Result) then
    Finish(RE, 'Invalid secret data');
end;

var
  MaxValue, MaxTries, Secret, Value, Iteration: Int64;
  Line: string;
begin
  MaxValue := ReadSecretValue;
  MaxTries := ReadSecretValue;
  Secret := ReadSecretValue;

  PrintLimits;

  WriteLn(MaxValue);
  Flush(Output);

  Iteration := 0;
  while ReadTrimmedLine(Line) do
  begin
    if (Length(Line) > 0) and (Line[1] = '!') then
    begin
      if not ParseInteger(Copy(Line, 2, Length(Line) - 1), Value) then
        Finish(RE, 'Invalid data');
      if Value = Secret then
        Finish(AC, '');
      Finish(WA, '!' + IntToStr(Secret));
    end;

    if Iteration > MaxTries then
      Finish(TLE, 'Exceeded number of allowed iterations');
    if not ParseInteger(Line, Value) then
      Finish(RE, 'Invalid data');

    if Secret < Value then
      WriteLn('<')
    else
      WriteLn('>=');
    Flush(Output);
    Inc(Iteration);
  end;

  Finish(RE, 'Unexpected flow');
end.
