program sample_validator
    use iso_fortran_env, only: output_unit, error_unit
    implicit none

    integer, parameter :: AC = 0
    integer, parameter :: WA = 1
    integer, parameter :: TLE = 2
    integer, parameter :: RE = 3
    integer, parameter :: PE = 4

    integer :: max_value, max_tries, secret, value, iteration, status
    character(len=4096) :: line
    character(len=64) :: message

    max_value = read_secret_value()
    max_tries = read_secret_value()
    secret = read_secret_value()

    call print_limits()

    write (output_unit, '(I0)') max_value
    flush (output_unit)

    iteration = 0
    do
        read (*, '(A)', iostat=status) line
        if (status /= 0) then
            call finish(RE, 'Unexpected flow')
        end if
        line = trim(adjustl(line))

        if (line(1:1) == '!') then
            value = parse_integer(line(2:), status)
            if (status /= 0) then
                call finish(RE, 'Invalid data')
            end if
            if (value == secret) then
                call finish(AC, '')
            end if
            write (message, '("!", I0)') secret
            call finish(WA, trim(message))
        end if

        if (iteration > max_tries) then
            call finish(TLE, 'Exceeded number of allowed iterations')
        end if

        value = parse_integer(line, status)
        if (status /= 0) then
            call finish(RE, 'Invalid data')
        end if

        if (secret < value) then
            write (output_unit, '(A)') '<'
        else
            write (output_unit, '(A)') '>='
        end if
        flush (output_unit)
        iteration = iteration + 1
    end do

contains

    subroutine finish(exitcode, message)
        integer, intent(in) :: exitcode
        character(len=*), intent(in) :: message

        if (len_trim(message) > 0) then
            write (error_unit, '(A)') trim(message)
            flush (error_unit)
        end if
        ! `call exit()` is a GNU extension that -std=f2018 rejects; a STOP code is the
        ! standard way to hand an exit status back to the judge.
        stop exitcode
    end subroutine finish

    integer function parse_integer(text, status) result(value)
        character(len=*), intent(in) :: text
        integer, intent(out) :: status
        character(len=len(text)) :: trimmed

        trimmed = adjustl(text)
        if (len_trim(trimmed) == 0) then
            status = 1
            value = 0
            return
        end if
        read (trimmed, *, iostat=status) value
    end function parse_integer

    integer function read_secret_value() result(value)
        character(len=4096) :: raw
        integer :: status

        read (*, '(A)', iostat=status) raw
        if (status /= 0) then
            call finish(RE, 'Invalid secret data')
        end if
        value = parse_integer(trim(adjustl(raw)), status)
        if (status /= 0) then
            call finish(RE, 'Invalid secret data')
        end if
    end function read_secret_value

    function env_number(name) result(value)
        character(len=*), intent(in) :: name
        character(len=64) :: value
        integer :: length, status

        call get_environment_variable(name, value, length, status)
        if (status /= 0 .or. length == 0) then
            value = 'null'
        end if
    end function env_number

    subroutine print_limits()
        character(len=64) :: language
        character(len=8192) :: per_language_limits
        character(len=8192) :: user_language
        integer :: length, status

        call get_environment_variable('USER_LANGUAGE', language, length, status)
        if (status /= 0 .or. length == 0) then
            user_language = 'null'
        else
            user_language = '"'//trim(language)//'"'
        end if

        call get_environment_variable('PER_LANGUAGE_LIMITS', per_language_limits, length, status)
        if (status /= 0 .or. length == 0) then
            per_language_limits = 'null'
        end if

        write (error_unit, '(A)') '{"problem_time_limit": '//trim(env_number('PROBLEM_TIME_LIMIT')) &
            //', "problem_output_limit": '//trim(env_number('PROBLEM_OUTPUT_LIMIT')) &
            //', "problem_memory_limit": '//trim(env_number('PROBLEM_MEMORY_LIMIT')) &
            //', "problem_pid_limit": '//trim(env_number('PROBLEM_PID_LIMIT')) &
            //', "user_language": '//trim(user_language) &
            //', "per_language_limits": '//trim(per_language_limits)//'}'
        flush (error_unit)
    end subroutine print_limits

end program sample_validator
