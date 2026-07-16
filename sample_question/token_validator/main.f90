!  NOCA -- Next Online Contest Administrator
!  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
!  This program is distributed in the hope that it will be useful,
!  but WITHOUT ANY WARRANTY; without even the implied warranty of
!  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

program route_optimizer
    implicit none

    integer, parameter :: max_k = 20
    integer, parameter :: inf = 1000000000

    type :: point
        integer :: x
        integer :: y
    end type point

    integer :: rows, columns, k
    integer :: i, j, u, v
    integer :: mask, next_mask, limit, full_mask
    integer :: cost, answer
    type(point) :: start_point, end_point
    type(point), dimension(0:max_k + 1) :: points
    integer, dimension(0:max_k + 1, 0:max_k + 1) :: dist
    integer, allocatable, dimension(:, :) :: dp

    read (*, *) rows, columns, k
    read (*, *) start_point%x, start_point%y
    read (*, *) end_point%x, end_point%y

    points(0) = start_point
    do i = 1, k
        read (*, *) points(i)%x, points(i)%y
    end do
    points(k + 1) = end_point

    if (k == 0) then
        write (*, '(I0)') manhattan(start_point, end_point)
        stop
    end if

    do i = 0, k + 1
        do j = 0, k + 1
            dist(i, j) = manhattan(points(i), points(j))
        end do
    end do

    limit = 2 ** k
    allocate(dp(0:limit - 1, 0:k - 1))
    dp = inf

    do i = 0, k - 1
        dp(2 ** i, i) = dist(0, i + 1)
    end do

    do mask = 0, limit - 1
        do u = 0, k - 1
            if (iand(mask, 2 ** u) == 0) cycle
            if (dp(mask, u) == inf) cycle

            do v = 0, k - 1
                if (iand(mask, 2 ** v) /= 0) cycle

                next_mask = ior(mask, 2 ** v)
                cost = dp(mask, u) + dist(u + 1, v + 1)
                if (cost < dp(next_mask, v)) then
                    dp(next_mask, v) = cost
                end if
            end do
        end do
    end do

    full_mask = limit - 1
    answer = inf
    do i = 0, k - 1
        cost = dp(full_mask, i) + dist(i + 1, k + 1)
        if (cost < answer) answer = cost
    end do

    write (*, '(I0)') answer

contains
    integer function manhattan(a, b)
        type(point), intent(in) :: a
        type(point), intent(in) :: b

        manhattan = abs(a%x - b%x) + abs(a%y - b%y)
    end function manhattan
end program route_optimizer
