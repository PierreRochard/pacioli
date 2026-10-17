package body Ofx.Tags with SPARK_Mode is

   function Name (T : Known_Tag) return String is
     (case T is
         when T_OFX             => "OFX",
         when T_SIGNONMSGSRSV1  => "SIGNONMSGSRSV1",
         when T_SONRS           => "SONRS",
         when T_STATUS          => "STATUS",
         when T_CODE            => "CODE",
         when T_SEVERITY        => "SEVERITY",
         when T_MESSAGE         => "MESSAGE",
         when T_DTSERVER        => "DTSERVER",
         when T_USERKEY         => "USERKEY",
         when T_TSKEYEXPIRE     => "TSKEYEXPIRE",
         when T_LANGUAGE        => "LANGUAGE",
         when T_DTPROFUP        => "DTPROFUP",
         when T_DTACCTUP        => "DTACCTUP",
         when T_FI              => "FI",
         when T_ORG             => "ORG",
         when T_FID             => "FID",
         when T_SESSCOOKIE      => "SESSCOOKIE",
         when T_ACCESSKEY       => "ACCESSKEY",
         when T_BANKMSGSRSV1    => "BANKMSGSRSV1",
         when T_CREDITCARDMSGSRSV1=> "CREDITCARDMSGSRSV1",
         when T_STMTTRNRS       => "STMTTRNRS",
         when T_CCSTMTTRNRS     => "CCSTMTTRNRS",
         when T_TRNUID          => "TRNUID",
         when T_CLTCOOKIE       => "CLTCOOKIE",
         when T_STMTRS          => "STMTRS",
         when T_CCSTMTRS        => "CCSTMTRS",
         when T_CURDEF          => "CURDEF",
         when T_BANKACCTFROM    => "BANKACCTFROM",
         when T_CCACCTFROM      => "CCACCTFROM",
         when T_BANKID          => "BANKID",
         when T_BRANCHID        => "BRANCHID",
         when T_ACCTID          => "ACCTID",
         when T_ACCTTYPE        => "ACCTTYPE",
         when T_ACCTKEY         => "ACCTKEY",
         when T_BANKTRANLIST    => "BANKTRANLIST",
         when T_DTSTART         => "DTSTART",
         when T_DTEND           => "DTEND",
         when T_STMTTRN         => "STMTTRN",
         when T_TRNTYPE         => "TRNTYPE",
         when T_DTPOSTED        => "DTPOSTED",
         when T_DTUSER          => "DTUSER",
         when T_DTAVAIL         => "DTAVAIL",
         when T_TRNAMT          => "TRNAMT",
         when T_FITID           => "FITID",
         when T_CORRECTFITID    => "CORRECTFITID",
         when T_CORRECTACTION   => "CORRECTACTION",
         when T_SRVRTID         => "SRVRTID",
         when T_CHECKNUM        => "CHECKNUM",
         when T_REFNUM          => "REFNUM",
         when T_SIC             => "SIC",
         when T_PAYEEID         => "PAYEEID",
         when T_NAME            => "NAME",
         when T_MEMO            => "MEMO",
         when T_INV401KSOURCE   => "INV401KSOURCE",
         when T_CURRENCY        => "CURRENCY",
         when T_ORIGCURRENCY    => "ORIGCURRENCY",
         when T_CURRATE         => "CURRATE",
         when T_CURSYM          => "CURSYM",
         when T_LEDGERBAL       => "LEDGERBAL",
         when T_AVAILBAL        => "AVAILBAL",
         when T_BALAMT          => "BALAMT",
         when T_DTASOF          => "DTASOF",
         when T_MKTGINFO        => "MKTGINFO",
         when T_BALLIST         => "BALLIST",
         when T_BANKTRANLISTP   => "BANKTRANLISTP",
         when T_REWARDINFO      => "REWARDINFO",
         when T_CASHADVBALAMT   => "CASHADVBALAMT",
         when T_INTRATEPURCH    => "INTRATEPURCH",
         when T_INTRATECASH     => "INTRATECASH",
         when T_INTRATEXFER     => "INTRATEXFER");

   function Lookup (Text : String; S : Span) return Tag is
   begin
      for T in Known_Tag loop
         if Matches (Text, S, Name (T)) then
            return T;
         end if;
      end loop;
      return T_Unknown;
   end Lookup;

end Ofx.Tags;
