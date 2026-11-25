-- =============================================================================
-- tb_proto_io.vhd - weryfikacja proto_io (SPI master, I2C master, UART, PVCC) przez porty Avalon
--   Modele: SPI slave (tryb 0 / 3, MISO opoznione o 30 ns - translatory + kabel),
--           I2C slave typu EEPROM pod adresem 0x50 (rozciaga SCL po ACK adresu),
--           UART: petla TX -> RX.
--   Questa:  cd sim ; vsim -c -do run_sim.do
-- =============================================================================
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_proto_io is
end entity;

architecture sim of tb_proto_io is

    constant TCLK : time := 20 ns;

    signal clk       : std_logic := '0';
    signal reset     : std_logic := '1';
    signal address   : std_logic_vector(2 downto 0) := (others => '0');
    signal read      : std_logic := '0';
    signal write     : std_logic := '0';
    signal writedata : std_logic_vector(31 downto 0) := (others => '0');
    signal readdata  : std_logic_vector(31 downto 0);

    signal sck_o, mosi_o, cs_o, miso_o, tx_o : std_logic;
    signal dir_spi, dir_miso, dir_tx, sda_drv, scl_drv : std_logic;
    signal pins_i    : std_logic_vector(7 downto 0);
    signal pvcc      : std_logic_vector(3 downto 0);

    -- SPI slave
    signal sl_mode3  : boolean := false;
    signal miso_bus  : std_logic := '0';
    type byte_arr is array (natural range <>) of std_logic_vector(7 downto 0);
    constant SPI_TX  : byte_arr(0 to 3) := (x"C3", x"5A", x"81", x"7E");
    signal spi_got   : byte_arr(0 to 7) := (others => x"00");
    signal spi_n     : integer := 0;

    -- I2C
    signal sda_bus, scl_bus : std_logic;
    signal sl_sda, sl_scl   : std_logic := 'Z';
    signal i2c_wr_n  : integer := 0;
    signal stretched : boolean := false;
    signal scl_mon   : boolean := false;                  -- pomiar czasow SCL w tescie I2C
    signal scl_lo_min, scl_hi_min : time := 1 sec;

    signal rx_line   : std_logic := '1';
    signal errors    : integer := 0;

begin

    clk <= not clk after TCLK / 2;

    dut : entity work.proto_io
        generic map (CLK_HZ => 50000000)
        port map (
            clk => clk, reset => reset,
            avs_address => address, avs_read => read, avs_write => write,
            avs_writedata => writedata, avs_readdata => readdata,
            sck_o => sck_o, mosi_o => mosi_o, cs_o => cs_o, miso_o => miso_o, tx_o => tx_o,
            dir_spi => dir_spi, dir_miso => dir_miso, dir_tx => dir_tx,
            sda_drv => sda_drv, scl_drv => scl_drv, pins_i => pins_i, pvcc_ctrl => pvcc
        );

    -- magistrala I2C: open drain + pull-up
    sda_bus <= 'H';
    scl_bus <= 'H';
    sda_bus <= '0' when sda_drv = '1' else 'Z';
    scl_bus <= '0' when scl_drv = '1' else 'Z';
    sda_bus <= sl_sda;
    scl_bus <= sl_scl;

    rx_line <= transport tx_o after 20 ns;
    pins_i <= to_x01(scl_bus) & to_x01(sda_bus) & rx_line & tx_o & miso_bus & cs_o & mosi_o & sck_o;

    -- ------------------------------------------------------------------ SPI slave
    p_spi_slave : process
        variable tb, rb : std_logic_vector(7 downto 0);
        variable k, nb  : integer := 0;
        variable first  : boolean;
    begin
        nb := 0;
        loop
            wait until falling_edge(cs_o);
            while cs_o = '0' loop
                tb := SPI_TX(nb mod 4);
                miso_bus <= transport tb(7) after 30 ns;
                first := true;
                for i in 7 downto 0 loop
                    wait until rising_edge(sck_o) or cs_o = '1';
                    exit when cs_o = '1';
                    rb(i) := mosi_o;
                    if i > 0 then
                        wait until falling_edge(sck_o) or cs_o = '1';
                        exit when cs_o = '1';
                        miso_bus <= transport tb(i - 1) after 30 ns;
                    end if;
                end loop;
                exit when cs_o = '1';
                spi_got(spi_n mod 8) <= rb;
                spi_n <= spi_n + 1;
                nb := nb + 1;
                -- koniec bajtu: zbocze ustawiajace nastepny bit (tryb 0: opadajace po 8. narastajacym)
                wait until falling_edge(sck_o) or cs_o = '1';
            end loop;
        end loop;
    end process;

    -- ------------------------------------------------------------------ pomiar SCL (tLOW / tHIGH)
    p_scl_mon : process
        variable t_edge : time := 0 ns;
    begin
        wait until scl_bus'event;
        if scl_mon and t_edge > 0 ns then
            if to_x01(scl_bus) = '1' then
                if now - t_edge < scl_lo_min then scl_lo_min <= now - t_edge; end if;
            else
                if now - t_edge < scl_hi_min then scl_hi_min <= now - t_edge; end if;
            end if;
        end if;
        t_edge := now;
    end process;

    -- ------------------------------------------------------------------ I2C slave (EEPROM 0x50)
    p_i2c_slave : process
        type mem_t is array (0 to 255) of std_logic_vector(7 downto 0);
        variable mem  : mem_t := (others => x"00");
        variable ptr  : integer range 0 to 255 := 0;
        variable b    : std_logic_vector(7 downto 0);
        variable bit1 : std_logic;
        variable ev   : integer;        -- 0 bit, 1 START, 2 STOP
        variable first_data : boolean;
        variable ack  : std_logic;

        procedure rx_bit(variable v : out std_logic; variable e : out integer) is
        begin
            wait until rising_edge(scl_bus);
            v := to_x01(sda_bus);
            wait until falling_edge(scl_bus) or sda_bus'event;
            if to_x01(scl_bus) = '1' then
                if to_x01(sda_bus) = '0' then e := 1; else e := 2; end if;
            else
                e := 0;
            end if;
        end procedure;

        procedure rx_byte(variable v : out std_logic_vector(7 downto 0); variable e : out integer) is
            variable x : std_logic;
            variable ee : integer;
        begin
            for i in 7 downto 0 loop
                rx_bit(x, ee);
                if ee /= 0 then
                    e := ee;
                    return;
                end if;
                v(i) := x;
            end loop;
            e := 0;
        end procedure;

        procedure send_ack is
        begin
            wait for 50 ns;
            sl_sda <= '0';
            wait until rising_edge(scl_bus);
            wait until falling_edge(scl_bus);
            wait for 50 ns;
            sl_sda <= 'Z';
        end procedure;

    begin
        sl_sda <= 'Z';
        sl_scl <= 'Z';
        wait until falling_edge(sda_bus) and to_x01(scl_bus) = '1';
        ev := 1;
        loop
            if ev = 2 then                                  -- STOP: czekaj na START
                wait until falling_edge(sda_bus) and to_x01(scl_bus) = '1';
            end if;
            -- po START: adres
            rx_byte(b, ev);
            next when ev /= 0;
            if b(7 downto 1) /= "1010000" then
                -- nie nasz adres: bez ACK, czekaj na START / STOP
                loop
                    rx_bit(bit1, ev);
                    exit when ev /= 0;
                end loop;
                next;
            end if;
            send_ack;
            if not stretched then                           -- rozciaganie SCL po pierwszym ACK
                sl_scl <= '0';
                wait for 3 us;
                sl_scl <= 'Z';
                stretched <= true;
            end if;
            if b(0) = '0' then
                first_data := true;
                loop
                    rx_byte(b, ev);
                    exit when ev /= 0;
                    if first_data then
                        ptr := to_integer(unsigned(b));
                        first_data := false;
                    else
                        mem(ptr) := b;
                        ptr := (ptr + 1) mod 256;
                        i2c_wr_n <= i2c_wr_n + 1;
                    end if;
                    send_ack;
                end loop;
            else
                loop
                    b := mem(ptr);
                    for i in 7 downto 0 loop
                        wait for 50 ns;
                        if b(i) = '0' then sl_sda <= '0'; else sl_sda <= 'Z'; end if;
                        wait until rising_edge(scl_bus);
                        wait until falling_edge(scl_bus);
                    end loop;
                    wait for 50 ns;
                    sl_sda <= 'Z';
                    wait until rising_edge(scl_bus);
                    ack := to_x01(sda_bus);
                    wait until falling_edge(scl_bus);
                    ptr := (ptr + 1) mod 256;
                    exit when ack = '1';                    -- NACK: koniec odczytu
                end loop;
                loop
                    rx_bit(bit1, ev);
                    exit when ev /= 0;
                end loop;
            end if;
        end loop;
    end process;

    -- ------------------------------------------------------------------ testy
    p_main : process

        procedure wr(a : integer; d : integer) is
        begin
            wait until rising_edge(clk);
            address   <= std_logic_vector(to_unsigned(a, 3));
            writedata <= std_logic_vector(to_signed(d, 32));
            write     <= '1';
            wait until rising_edge(clk);
            write     <= '0';
        end procedure;

        procedure wru(a : integer; d : unsigned(31 downto 0)) is
        begin
            wait until rising_edge(clk);
            address   <= std_logic_vector(to_unsigned(a, 3));
            writedata <= std_logic_vector(d);
            write     <= '1';
            wait until rising_edge(clk);
            write     <= '0';
        end procedure;

        procedure rd(a : integer; d : out std_logic_vector(31 downto 0)) is
        begin
            wait until rising_edge(clk);
            address <= std_logic_vector(to_unsigned(a, 3));
            read    <= '1';
            wait until rising_edge(clk);
            read    <= '0';
            wait until rising_edge(clk);
            wait for 1 ns;
            d := readdata;
        end procedure;

        procedure check(what : string; got, exp : integer) is
        begin
            if got /= exp then
                report "BLAD " & what & ": jest " & integer'image(got) & ", oczekiwano " & integer'image(exp)
                    severity error;
                errors <= errors + 1;
                wait for 0 ns;
            end if;
        end procedure;

        procedure wait_idle(tmax : time) is
            variable d : std_logic_vector(31 downto 0);
            variable t : time := now;
        begin
            loop
                rd(5, d);
                exit when d(20) = '0';
                if now - t > tmax then
                    report "BLAD: timeout (zajety)" severity error;
                    errors <= errors + 1;
                    exit;
                end if;
            end loop;
        end procedure;

        procedure pop_rx(what : string; exp : integer) is
            variable d : std_logic_vector(31 downto 0);
        begin
            rd(4, d);
            check(what & " (wpis)", to_integer(unsigned'('0' & d(31))), 1);
            check(what, to_integer(unsigned(d(8 downto 0))), exp);
        end procedure;

        variable d : std_logic_vector(31 downto 0);
        variable n0 : integer;

    begin
        wait for 5 * TCLK;
        wait until rising_edge(clk);
        reset <= '0';
        wait for 3 * TCLK;

        -- ------------------------------------------------ 1. ID, stan po resecie
        rd(0, d); check("ID", to_integer(unsigned(d(30 downto 0))), 16#50544F31#);
        check("dir_spi po resecie", to_integer(unsigned'('0' & dir_spi)), 0);
        check("dir_tx po resecie", to_integer(unsigned'('0' & dir_tx)), 0);
        check("sda_drv po resecie", to_integer(unsigned'('0' & sda_drv)), 0);
        rd(4, d); check("RX pusta", to_integer(unsigned(d(31 downto 31))), 0);

        -- ------------------------------------------------ 2. SPI master tryb 0, 12,5 MHz, opoznienie probkowania 1
        wr(2, 1);
        wr(1, 1 + 1 * 512);                                -- SPI master, sdel = 1
        wait for 2 * TCLK;                                 -- dir_* rejestrowane (1 takt)
        check("dir_spi SPI", to_integer(unsigned'('0' & dir_spi)), 1);
        n0 := spi_n;
        wr(3, 16#9F#);
        wr(3, 16#12#);
        wr(3, 16#100# + 16#34#);                           -- ostatni bajt -> CS = 1
        wait_idle(50 us);
        check("SPI: CS po transakcji", to_integer(unsigned'('0' & cs_o)), 1);
        check("SPI: bajty u slave", spi_n - n0, 3);
        check("SPI MOSI 0", to_integer(unsigned(spi_got(n0 mod 8))), 16#9F#);
        check("SPI MOSI 1", to_integer(unsigned(spi_got((n0 + 1) mod 8))), 16#12#);
        check("SPI MOSI 2", to_integer(unsigned(spi_got((n0 + 2) mod 8))), 16#34#);
        pop_rx("SPI MISO 0", 16#C3#);
        pop_rx("SPI MISO 1", 16#5A#);
        pop_rx("SPI MISO 2", 16#81#);
        rd(5, d); check("SPI: RX pusta", to_integer(unsigned(d(19 downto 10))), 0);

        -- ------------------------------------------------ 2b. HOLD: kolejka ladowana bez startu
        n0 := spi_n;
        wr(5, 1);
        wr(3, 16#11#);
        wr(3, 16#100# + 16#22#);
        wait for 5 us;
        check("HOLD: brak transmisji", spi_n - n0, 0);
        rd(5, d); check("HOLD: 2 wpisy w TX", to_integer(unsigned(d(9 downto 0))), 2);
        wr(5, 0);
        wait_idle(50 us);
        check("HOLD: po zwolnieniu 2 bajty", spi_n - n0, 2);
        pop_rx("HOLD MISO 0", 16#7E#);
        pop_rx("HOLD MISO 1", 16#C3#);
        rd(7, d); n0 := to_integer(unsigned(d(31 downto 8)));
        wait for 10 us;
        rd(7, d); check("licznik us (+10)", to_integer(unsigned(d(31 downto 8))) - n0, 10);

        -- ------------------------------------------------ 3. SPI tryb 3, LSB first, 5 MHz (div 4)
        wr(2, 4);
        wr(1, 1 + 8 + 16 + 32 + 1 * 512);
        n0 := spi_n;
        wr(3, 16#01#);                                     -- LSB first: na linii 0x80
        wr(3, 16#100# + 16#C0#);                           -- na linii 0x03
        wait_idle(50 us);
        check("SPI3: bajty", spi_n - n0, 2);
        check("SPI3 MOSI 0", to_integer(unsigned(spi_got(n0 mod 8))), 16#80#);
        check("SPI3 MOSI 1", to_integer(unsigned(spi_got((n0 + 1) mod 8))), 16#03#);
        pop_rx("SPI3 MISO 0", 16#5A#);                     -- slave: 6. bajt (0x5A, symetryczny bitowo)
        pop_rx("SPI3 MISO 1", 16#81#);                     -- 0x81 (symetryczny bitowo)
        check("SCK spoczynek CPOL=1", to_integer(unsigned'('0' & sck_o)), 1);

        -- ------------------------------------------------ 4. I2C master 1 MHz (div 9): zapis, odczyt, zly adres
        -- SCL = 50 MHz / (5 * 10): stan niski 3 U = 600 ns, wysoki 2 U = 400 ns (U = 200 ns)
        wr(2, 9);
        scl_mon <= true;
        wr(1, 2);
        wait for 2 * TCLK;                                 -- dir_* rejestrowane (1 takt)
        check("dir_spi I2C", to_integer(unsigned'('0' & dir_spi)), 0);
        wr(3, 16#301#);                                    -- START
        wr(3, 16#0A0#);                                    -- adres 0x50, zapis
        wr(3, 16#010#);                                    -- wskaznik 0x10
        wr(3, 16#022#);
        wr(3, 16#033#);
        wr(3, 16#302#);                                    -- STOP
        wait_idle(200 us);
        pop_rx("I2C ACK adres", 0);
        pop_rx("I2C ACK wskaznik", 0);
        pop_rx("I2C ACK dane 1", 0);
        pop_rx("I2C ACK dane 2", 0);
        check("I2C: zapisane bajty", i2c_wr_n, 2);
        check("I2C: SCL zwolnione", to_integer(unsigned'('0' & scl_drv)), 0);
        check("I2C: SDA zwolnione", to_integer(unsigned'('0' & sda_drv)), 0);
        -- odczyt: START A0 10 RESTART A1 rd-ACK rd-NACK STOP
        wr(3, 16#301#);
        wr(3, 16#0A0#);
        wr(3, 16#010#);
        wr(3, 16#301#);
        wr(3, 16#0A1#);
        wr(3, 16#100#);                                    -- odczyt z ACK
        wr(3, 16#200#);                                    -- odczyt z NACK
        wr(3, 16#302#);
        wait_idle(200 us);
        pop_rx("I2C rd ACK adres", 0);
        pop_rx("I2C rd ACK wskaznik", 0);
        pop_rx("I2C rd ACK adres R", 0);
        pop_rx("I2C odczyt 1", 16#22#);
        pop_rx("I2C odczyt 2", 16#33#);
        -- brak urzadzenia: NACK
        wr(3, 16#301#);
        wr(3, 16#0B0#);
        wr(3, 16#302#);
        wait_idle(100 us);
        pop_rx("I2C NACK (brak 0x58)", 16#100#);
        rd(5, d); check("I2C: brak bledu magistrali", to_integer(unsigned'('0' & d(22))), 0);
        scl_mon <= false;
        check("I2C: min tLOW >= 600 ns", boolean'pos(scl_lo_min >= 600 ns), 1);
        check("I2C: min tHIGH >= 400 ns", boolean'pos(scl_hi_min >= 400 ns), 1);
        report "I2C 1 MHz: min tLOW = " & time'image(scl_lo_min) & ", min tHIGH = " & time'image(scl_hi_min);

        -- ------------------------------------------------ 5. UART 3 Mbd, parzystosc parzysta, petla
        wru(2, unsigned'(x"F5C28F5C"));                   -- 16 * 3e6 * 2^32 / 50e6 = 4123168604
        wr(1, 3 + 64);
        wait for 2 * TCLK;                                 -- dir_* rejestrowane (1 takt)
        check("dir_tx UART", to_integer(unsigned'('0' & dir_tx)), 1);
        wr(3, 16#55#);
        wr(3, 16#00#);
        wr(3, 16#FF#);
        wr(3, 16#A7#);
        wait_idle(50 us);
        wait for 5 us;
        pop_rx("UART 0x55", 16#55#);
        pop_rx("UART 0x00", 16#00#);
        pop_rx("UART 0xFF", 16#FF#);
        pop_rx("UART 0xA7", 16#A7#);
        -- 115200 Bd, 8N1, 2 bity stopu
        wru(2, to_unsigned(158329674, 32));                 -- 16 * 115200 * 2^32 / 50e6
        wr(1, 3 + 256);
        wr(3, 16#3C#);
        wait_idle(200 us);
        wait for 10 us;
        pop_rx("UART 115200 0x3C", 16#3C#);

        -- ------------------------------------------------ 6. PVCC
        wr(6, 16#5#);                                      -- 3,3 V
        wait for 1 us;
        check("PVCC: wylaczone, sel 3V3", to_integer(unsigned(pvcc)), 4);
        wait for 2600 us;
        check("PVCC: 3,3 V wlaczone", to_integer(unsigned(pvcc)), 5);
        wr(6, 0);
        wait for 2600 us;
        check("PVCC: wylaczone", to_integer(unsigned(pvcc)), 0);
        wr(1, 0);
        wait for 2 * TCLK;                                 -- dir_* rejestrowane (1 takt)
        check("tryb 0: dir_tx", to_integer(unsigned'('0' & dir_tx)), 0);

        wait for 1 us;
        if errors = 0 then
            report "=== TEST PASSED ===" severity note;
        else
            report "=== TEST FAILED: " & integer'image(errors) & " bledow ===" severity error;
        end if;
        std.env.finish;
    end process;

end architecture;
