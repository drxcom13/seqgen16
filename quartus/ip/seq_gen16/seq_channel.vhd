-- =============================================================================
-- seq_channel.vhd  -  jeden kanal cyfrowego generatora sekwencji (1 blok = 1 kanal)
--
--  * pamiec sekwencji 2**AW krokow x 1 bit (domyslnie 1024 x 1 = 1 blok M9K)
--    - port A: magistrala (zapis/odczyt z Nios przez Avalon)
--    - port B: odczyt przez sekwencer
--  * krok czasowy  = (cfg_period + 1) us              (1 ms -> 999, 1 s -> 999_999)
--  * dlugosc       = cfg_last + 1 krokow (1..1024)
--  * cfg_loops     : 0 = petla bez konca, N = N powtorzen sekwencji
--  * wyjscie       : cyfrowe 0/1 (rejestrowane, bez glitchy)
--
--  Opoznienie start -> zmiana wyjscia jest stale i jednakowe dla wszystkich
--  kanalow, wiec kanaly startowane jednym zapisem sa zsynchronizowane co do taktu.
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity seq_channel is
    generic (
        AW : positive := 10;    -- adres kroku (1024 kroki)
        PW : positive := 20     -- licznik okresu w us (2^20 us = 1.048 s)
    );
    port (
        clk              : in  std_logic;
        rst              : in  std_logic;
        tick_us          : in  std_logic;                 -- impuls co 1 us (wspolny dla kanalow)
        -- port magistrali do pamieci sekwencji
        bus_we           : in  std_logic;
        bus_addr         : in  std_logic_vector(AW-1 downto 0);
        bus_wdata        : in  std_logic;
        bus_rdata        : out std_logic;
        -- konfiguracja
        cfg_period       : in  unsigned(PW-1 downto 0);   -- mikrosekundy na krok - 1
        cfg_last         : in  unsigned(AW-1 downto 0);   -- liczba krokow - 1
        cfg_loops        : in  unsigned(15 downto 0);     -- 0 = nieskonczenie
        cfg_idle         : in  std_logic;                 -- stan wyjscia w spoczynku
        cfg_idle_on_done : in  std_logic;                 -- 1 = po koncu idle, 0 = trzymaj ostatni
        -- sterowanie (impulsy 1 takt)
        start            : in  std_logic;
        stop             : in  std_logic;
        -- status / wyjscie
        running          : out std_logic;
        done             : out std_logic;
        index            : out unsigned(AW-1 downto 0);
        dout             : out std_logic
    );
end entity;

architecture rtl of seq_channel is

    type mem_t is array (0 to 2**AW - 1) of std_logic;
    -- wzorzec Quartusa "true dual port": zmienna wspoldzielona + 2 procesy -> 1 blok M9K na kanal
    -- (z sygnalem Quartus tworzyl 2 kopie pamieci, 32 M9K na 16 kanalow)
    shared variable mem : mem_t;

    signal q          : std_logic;
    signal run_r      : std_logic := '0';
    signal done_r     : std_logic := '0';
    signal idx        : unsigned(AW-1 downto 0) := (others => '0');
    signal cnt        : unsigned(PW-1 downto 0) := (others => '0');
    signal loop_cnt   : unsigned(15 downto 0)   := (others => '0');
    signal sel_d      : std_logic := '0';
    signal dout_r     : std_logic := '0';

begin

    -- ------------------------------------------------------------ pamiec (M9K)
    p_ram_a : process (clk)
    begin
        if rising_edge(clk) then
            if bus_we = '1' then
                mem(to_integer(unsigned(bus_addr))) := bus_wdata;
            end if;
            bus_rdata <= mem(to_integer(unsigned(bus_addr)));
        end if;
    end process;

    p_ram_b : process (clk)
    begin
        if rising_edge(clk) then
            q <= mem(to_integer(idx));
        end if;
    end process;

    -- ------------------------------------------------------------ sekwencer
    p_seq : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                run_r    <= '0';
                done_r   <= '0';
                idx      <= (others => '0');
                cnt      <= (others => '0');
                loop_cnt <= (others => '0');
            elsif stop = '1' then
                run_r    <= '0';
                done_r   <= '0';
            elsif start = '1' then
                run_r    <= '1';
                done_r   <= '0';
                idx      <= (others => '0');
                cnt      <= (others => '0');
                loop_cnt <= (others => '0');
            elsif run_r = '1' and tick_us = '1' then
                -- ">=" zamiast "=" - bezpieczne przy zmianie okresu/dlugosci w trakcie pracy
                if cnt >= cfg_period then
                    cnt <= (others => '0');
                    if idx >= cfg_last then
                        if cfg_loops /= 0 and (loop_cnt + 1) >= cfg_loops then
                            run_r  <= '0';           -- koniec; idx zostaje na ostatnim kroku
                            done_r <= '1';
                        else
                            idx      <= (others => '0');
                            loop_cnt <= loop_cnt + 1;
                        end if;
                    else
                        idx <= idx + 1;
                    end if;
                else
                    cnt <= cnt + 1;
                end if;
            end if;
        end if;
    end process;

    -- ------------------------------------------------------------ wyjscie
    -- sel_d opozniony o 1 takt, zeby zgadzal sie z latencja odczytu RAM (q)
    p_out : process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                sel_d  <= '0';
                dout_r <= '0';
            else
                sel_d <= run_r or (done_r and not cfg_idle_on_done);
                if sel_d = '1' then
                    dout_r <= q;
                else
                    dout_r <= cfg_idle;
                end if;
            end if;
        end if;
    end process;

    running <= run_r;
    done    <= done_r;
    index   <= idx;
    dout    <= dout_r;

end architecture;
