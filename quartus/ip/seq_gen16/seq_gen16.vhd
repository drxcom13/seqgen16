-- =============================================================================
-- seq_gen16.vhd  -  16-kanalowy cyfrowy generator sekwencji, slave Avalon-MM
--
-- Magistrala: 32 bit, adresowanie slowami (address = offset_bajtowy / 4),
--             readLatency = 2 (staly), zapis bez waitrequest.
--
-- Mapa rejestrow (offset w SLOWACH):
--   0x0000          ID        R   0x53513136 ("SQ16")
--   0x0001          START     W   maska kanalow -> start (synchroniczny, ten sam takt)
--   0x0002          STOP      W   maska kanalow -> stop  (wyjscie = idle)
--   0x0003          RUNNING   R   maska kanalow pracujacych
--   0x0004          DONE      R   maska kanalow zakonczonych (kasowane przez START/STOP)
--   0x0005          CLK_HZ    R   czestotliwosc zegara
--   0x0006          PARAMS    R   [7:0]=NCH [15:8]=AW [23:16]=1 (bit/krok) [31:24]=PW
--   0x0007          BCLK_HALF W   polowa okresu zegara bazowego w us - 1 (1 ms -> 499)
--   0x0008          SYNC_W    W   szerokosc impulsu SYNC w us (0 = SYNC wysoki dopoki
--                                 pracuje dowolny kanal)
--
-- START (dowolna maska) zeruje tez podstawe czasu 1 us, zegar bazowy i wyzwala SYNC,
-- wiec pierwszy krok trwa dokladnie okres, a zbocza BASE_CLK/SYNC wypadaja w tym samym
-- takcie zegara co zmiany wyjsc kanalow (identyczne opoznienie potokowe).
--   0x0040+4*ch+0   PERIOD    W   mikrosekundy na krok - 1 (PW bit; 1 ms = 999)
--   0x0040+4*ch+1   LAST      W   liczba krokow - 1        (AW bit)
--   0x0040+4*ch+2   CFG       W   [15:0]=loops (0=inf) [16]=idle [25]=idle_on_done
--   0x0040+4*ch+3   STATUS    R   [9:0]=index [16]=running [17]=done [24]=wyjscie
--   0x4000 + ch*1024 + i  STEP RW  [0] = stan wyjscia w kroku i kanalu ch
--
-- Rejestry PERIOD/LAST/CFG sa tylko do zapisu (oszczednosc multipleksera
-- odczytu); firmware przechowuje ich kopie.
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity seq_gen16 is
    generic (
        NCH    : positive := 16;
        AW     : positive := 10;
        PW     : positive := 20;            -- okres w us: 2^20 us = 1.048 s
        CLK_HZ : positive := 50000000
    );
    port (
        clk           : in  std_logic;
        reset         : in  std_logic;
        avs_address   : in  std_logic_vector(14 downto 0);
        avs_read      : in  std_logic;
        avs_write     : in  std_logic;
        avs_writedata : in  std_logic_vector(31 downto 0);
        avs_readdata  : out std_logic_vector(31 downto 0);
        ch_out        : out std_logic_vector(NCH-1 downto 0);
        ch_running    : out std_logic_vector(NCH-1 downto 0);
        clk_base      : out std_logic;     -- zegar bazowy zsynchronizowany z wyjsciami
        sync_out      : out std_logic      -- zbocze narastajace w chwili wyzwolenia sekwencji
    );
end entity;

architecture rtl of seq_gen16 is

    constant ID : std_logic_vector(31 downto 0) := x"53513136";

    type slv_32_arr  is array (0 to NCH-1) of std_logic_vector(31 downto 0);
    type per_arr     is array (0 to NCH-1) of unsigned(PW-1 downto 0);
    type idx_arr     is array (0 to NCH-1) of unsigned(AW-1 downto 0);
    type loops_arr   is array (0 to NCH-1) of unsigned(15 downto 0);

    signal period       : per_arr;
    signal last         : idx_arr;
    signal loops        : loops_arr;
    signal idle         : std_logic_vector(NCH-1 downto 0);
    signal idle_on_done : std_logic_vector(NCH-1 downto 0);

    signal ch_rdata     : std_logic_vector(NCH-1 downto 0);
    signal ch_index     : idx_arr;
    signal ch_stat      : slv_32_arr;
    signal run_v        : std_logic_vector(NCH-1 downto 0);
    signal done_v       : std_logic_vector(NCH-1 downto 0);
    signal out_v        : std_logic_vector(NCH-1 downto 0);

    signal start_p      : std_logic_vector(NCH-1 downto 0) := (others => '0');
    signal stop_p       : std_logic_vector(NCH-1 downto 0) := (others => '0');
    signal raddr_d      : std_logic_vector(14 downto 0) := (others => '0');

    constant US_DIV     : positive := CLK_HZ / 1000000;     -- 50 taktow = 1 us
    signal presc        : integer range 0 to US_DIV - 1 := 0;
    signal tick_us      : std_logic := '0';

    signal start_any    : std_logic;
    signal bclk_half    : unsigned(PW-1 downto 0) := to_unsigned(499, PW);
    signal sync_w       : unsigned(PW-1 downto 0) := to_unsigned(1000, PW);
    signal bc_cnt       : unsigned(PW-1 downto 0) := (others => '0');
    signal bc_lvl       : std_logic := '0';
    signal sy_cnt       : unsigned(PW-1 downto 0) := (others => '0');
    signal sy_lvl       : std_logic := '0';
    -- 2 rejestry opoznienia = to samo opoznienie co idx -> q -> dout w kanale
    signal bc_d         : std_logic_vector(1 downto 0) := (others => '0');
    signal sy_d         : std_logic_vector(1 downto 0) := (others => '0');

    signal is_mem       : std_logic;
    signal is_chr       : std_logic;
    signal mem_ch       : integer range 0 to 15;
    signal reg_ch       : integer range 0 to 15;
    signal reg_sel      : std_logic_vector(1 downto 0);

begin

    is_mem  <= avs_address(14);
    is_chr  <= '1' when avs_address(14) = '0' and avs_address(13 downto 6) = x"01" else '0';
    mem_ch  <= to_integer(unsigned(avs_address(13 downto 10)));
    reg_ch  <= to_integer(unsigned(avs_address(5 downto 2)));
    reg_sel <= avs_address(1 downto 0);

    -- ---------------------------------------------------- wspolna podstawa czasu 1 us
    -- START restartuje podstawe czasu: kolejne granice krokow wypadaja dokladnie co
    -- N * 1 us od wyzwolenia (pierwszy krok nie jest skracany).
    start_any <= '1' when start_p /= (start_p'range => '0') else '0';

    p_presc : process (clk)
    begin
        if rising_edge(clk) then
            tick_us <= '0';
            if start_any = '1' then
                presc <= 1;
            elsif presc = US_DIV - 1 then
                presc   <= 0;
                tick_us <= '1';
            else
                presc <= presc + 1;
            end if;
        end if;
    end process;

    -- ---------------------------------------------------- impulsy start/stop
    p_cmd : process (clk)
    begin
        if rising_edge(clk) then
            start_p <= (others => '0');
            stop_p  <= (others => '0');
            if reset = '0' and avs_write = '1' then
                if unsigned(avs_address) = 1 then
                    start_p <= avs_writedata(NCH-1 downto 0);
                elsif unsigned(avs_address) = 2 then
                    stop_p  <= avs_writedata(NCH-1 downto 0);
                end if;
            end if;
        end if;
    end process;

    -- ---------------------------------------------------- rejestry konfiguracji
    p_cfg : process (clk)
    begin
        if rising_edge(clk) then
            if reset = '1' then
                for c in 0 to NCH-1 loop
                    period(c)       <= to_unsigned(999, PW);                 -- 1 ms
                    last(c)         <= (others => '1');                      -- 1024 kroki
                    loops(c)        <= (others => '0');
                    idle(c)         <= '0';
                    idle_on_done(c) <= '0';
                end loop;
                bclk_half <= to_unsigned(499, PW);                          -- 1 ms
                sync_w    <= to_unsigned(1000, PW);                         -- 1 ms
            elsif avs_write = '1' and unsigned(avs_address) = 7 then
                bclk_half <= unsigned(avs_writedata(PW-1 downto 0));
            elsif avs_write = '1' and unsigned(avs_address) = 8 then
                sync_w    <= unsigned(avs_writedata(PW-1 downto 0));
            elsif avs_write = '1' and is_chr = '1' then
                case reg_sel is
                    when "00" => period(reg_ch) <= unsigned(avs_writedata(PW-1 downto 0));
                    when "01" => last(reg_ch)   <= unsigned(avs_writedata(AW-1 downto 0));
                    when "10" =>
                        loops(reg_ch)        <= unsigned(avs_writedata(15 downto 0));
                        idle(reg_ch)         <= avs_writedata(16);
                        idle_on_done(reg_ch) <= avs_writedata(25);
                    when others => null;
                end case;
            end if;
        end if;
    end process;

    -- ---------------------------------------------------- kanaly (1 blok = 1 kanal)
    g_ch : for c in 0 to NCH-1 generate
        signal we : std_logic;
    begin
        we <= '1' when avs_write = '1' and is_mem = '1' and mem_ch = c else '0';

        u_ch : entity work.seq_channel
            generic map (AW => AW, PW => PW)
            port map (
                clk              => clk,
                rst              => reset,
                tick_us          => tick_us,
                bus_we           => we,
                bus_addr         => avs_address(AW-1 downto 0),
                bus_wdata        => avs_writedata(0),
                bus_rdata        => ch_rdata(c),
                cfg_period       => period(c),
                cfg_last         => last(c),
                cfg_loops        => loops(c),
                cfg_idle         => idle(c),
                cfg_idle_on_done => idle_on_done(c),
                start            => start_p(c),
                stop             => stop_p(c),
                running          => run_v(c),
                done             => done_v(c),
                index            => ch_index(c),
                dout             => out_v(c)
            );

        ch_stat(c) <= "0000000" & out_v(c) & "000000" & done_v(c) & run_v(c)
                      & std_logic_vector(to_unsigned(0, 16 - AW)) & std_logic_vector(ch_index(c));
    end generate;

    ch_out     <= out_v;
    ch_running <= run_v;

    -- ---------------------------------------------------- zegar bazowy i SYNC
    -- bc_lvl / sy_lvl zmieniaja sie na tych samych zboczach co indeksy kanalow
    -- (START i granice krokow), a 2 rejestry opoznienia wyrownuja je z dout kanalow.
    p_aux : process (clk)
    begin
        if rising_edge(clk) then
            if reset = '1' then
                bc_cnt <= (others => '0');
                bc_lvl <= '0';
                sy_cnt <= (others => '0');
                sy_lvl <= '0';
            elsif start_any = '1' then
                bc_cnt <= (others => '0');
                bc_lvl <= '1';                       -- zbocze narastajace = start sekwencji
                sy_cnt <= (others => '0');
                sy_lvl <= '1';
            elsif tick_us = '1' then
                if bc_cnt >= bclk_half then
                    bc_cnt <= (others => '0');
                    bc_lvl <= not bc_lvl;
                else
                    bc_cnt <= bc_cnt + 1;
                end if;
                if sy_lvl = '1' and sync_w /= 0 then
                    if sy_cnt >= sync_w - 1 then
                        sy_lvl <= '0';
                    else
                        sy_cnt <= sy_cnt + 1;
                    end if;
                end if;
            end if;

            -- Przy START pierwszy rejestr opoznienia jest zerowany na 1 takt: jesli wyjscie
            -- bylo w stanie 1 (restart w trakcie pracy), schodzi do 0 na 20 ns, dzieki czemu
            -- zbocze narastajace ZAWSZE wypada w chwili wyzwolenia sekwencji.
            -- BASE_CLK nadaje tylko gdy pracuje dowolny kanal (w spoczynku = 0).
            if start_any = '1' then
                bc_d <= bc_d(0) & '0';
                sy_d <= sy_d(0) & '0';
            else
                if run_v /= (run_v'range => '0') then
                    bc_d <= bc_d(0) & bc_lvl;
                else
                    bc_d <= bc_d(0) & '0';
                end if;
                if sync_w = 0 then                   -- tryb: wysoki dopoki pracuje kanal
                    if run_v /= (run_v'range => '0') then
                        sy_d <= sy_d(0) & '1';
                    else
                        sy_d <= sy_d(0) & '0';
                    end if;
                else
                    sy_d <= sy_d(0) & sy_lvl;
                end if;
            end if;
        end if;
    end process;

    clk_base <= bc_d(1);
    sync_out <= sy_d(1);

    -- ---------------------------------------------------- odczyt (latencja 2)
    -- takt 1: RAM kanalow odczytuje bus_addr, rejestrujemy adres
    -- takt 2: multiplekser -> avs_readdata
    p_rd : process (clk)
        variable a : std_logic_vector(14 downto 0);
    begin
        if rising_edge(clk) then
            raddr_d <= avs_address;
            a := raddr_d;
            avs_readdata <= (others => '0');
            if a(14) = '1' then
                avs_readdata(0) <= ch_rdata(to_integer(unsigned(a(13 downto 10))));
            elsif a(13 downto 6) = x"01" then
                if a(1 downto 0) = "11" then
                    avs_readdata <= ch_stat(to_integer(unsigned(a(5 downto 2))));
                end if;
            else
                case to_integer(unsigned(a(13 downto 0))) is
                    when 0 => avs_readdata <= ID;
                    when 3 => avs_readdata(NCH-1 downto 0) <= run_v;
                    when 4 => avs_readdata(NCH-1 downto 0) <= done_v;
                    when 5 => avs_readdata <= std_logic_vector(to_unsigned(CLK_HZ, 32));
                    when 6 => avs_readdata <= std_logic_vector(to_unsigned(PW, 8)) &
                                              x"01" &
                                              std_logic_vector(to_unsigned(AW, 8)) &
                                              std_logic_vector(to_unsigned(NCH, 8));
                    when others => null;
                end case;
            end if;
        end if;
    end process;

end architecture;
