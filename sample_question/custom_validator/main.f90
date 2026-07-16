program binary_search
    implicit none
    integer :: lower, upper, number
    character(len=16) :: answer

    lower = 1
    read(*, *) upper

    do while (lower < upper)
        number = lower + (upper - lower) / 2 + 1
        write(*, '(I0)') number
        flush(6)

        read(*, '(A)') answer
        answer = trim(adjustl(answer))

        if (trim(answer) == '<') then
            upper = number - 1
        else if (trim(answer) == '>=') then
            lower = number
        else
            write(0, *) 'Invalid answer from validator: ', trim(answer)
            stop 1
        end if
    end do

    write(*, '("!", I0)') lower
    flush(6)
    stop
end program binary_search
