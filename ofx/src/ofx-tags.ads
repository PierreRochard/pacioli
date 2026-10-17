--  The OFX element names this component recognizes.

package Ofx.Tags with SPARK_Mode is

   type Tag is
     (T_Unknown,
      T_OFX, T_SIGNONMSGSRSV1, T_SONRS, T_STATUS, T_CODE,
      T_SEVERITY, T_MESSAGE, T_DTSERVER, T_USERKEY, T_TSKEYEXPIRE,
      T_LANGUAGE, T_DTPROFUP, T_DTACCTUP, T_FI, T_ORG,
      T_FID, T_SESSCOOKIE, T_ACCESSKEY, T_BANKMSGSRSV1, T_CREDITCARDMSGSRSV1,
      T_STMTTRNRS, T_CCSTMTTRNRS, T_TRNUID, T_CLTCOOKIE, T_STMTRS,
      T_CCSTMTRS, T_CURDEF, T_BANKACCTFROM, T_CCACCTFROM, T_BANKID,
      T_BRANCHID, T_ACCTID, T_ACCTTYPE, T_ACCTKEY, T_BANKTRANLIST,
      T_DTSTART, T_DTEND, T_STMTTRN, T_TRNTYPE, T_DTPOSTED,
      T_DTUSER, T_DTAVAIL, T_TRNAMT, T_FITID, T_CORRECTFITID,
      T_CORRECTACTION, T_SRVRTID, T_CHECKNUM, T_REFNUM, T_SIC,
      T_PAYEEID, T_NAME, T_MEMO, T_INV401KSOURCE, T_CURRENCY,
      T_ORIGCURRENCY, T_CURRATE, T_CURSYM, T_LEDGERBAL, T_AVAILBAL,
      T_BALAMT, T_DTASOF, T_MKTGINFO, T_BALLIST, T_BANKTRANLISTP,
      T_REWARDINFO, T_CASHADVBALAMT, T_INTRATEPURCH, T_INTRATECASH, T_INTRATEXFER);

   subtype Known_Tag is Tag range T_OFX .. Tag'Last;

   function Name (T : Known_Tag) return String
   with Post => Name'Result'First = 1 and then Name'Result'Length in 1 .. 24;

   --  The tag whose name is Text (S), or T_Unknown.
   function Lookup (Text : String; S : Span) return Tag
   with Pre => Within (S, Text);

   --  Private extensions such as INTU.BID carry a '.' in their name.
   function Is_Private (Text : String; S : Span) return Boolean is
     (for some I in S.First .. S.Last => Text (I) = '.')
   with Pre => Within (S, Text);

end Ofx.Tags;
